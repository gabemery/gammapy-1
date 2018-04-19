# Licensed under a 3-clause BSD style license - see LICENSE.rst
from collections import OrderedDict
import numpy as np
import astropy.units as u
from astropy.table import Table
from astropy.time import Time
from ..spectrum.utils import CountsPredictor
from ..stats.poisson import excess_error
from ..utils.scripts import make_path
from ..stats.poisson import significance_on_off

__all__ = [
    'LightCurve',
    'LightCurveEstimator',
]


class LightCurve(object):
    """Lightcurve container.

    The lightcurve data is stored in ``table``.

    For now we only support times stored in MJD format!

    TODO: specification of format is work in progress
    See https://github.com/open-gamma-ray-astro/gamma-astro-data-formats/pull/61

    Usage: :ref:`time-lc`

    Parameters
    ----------
    table : `~astropy.table.Table`
        Table with lightcurve data
    """

    def __init__(self, table):
        self.table = table

    def __repr__(self):
        return '{}(len={})'.format(self.__class__.__name__, len(self.table))

    @property
    def time_scale(self):
        """Time scale (str).

        Taken from table "TIMESYS" header.
        Common values: "TT" or "UTC".
        Assumed default is "UTC".
        """
        return self.table.meta.get('TIMESYS', 'utc')

    @property
    def time_format(self):
        """Time format (str)."""
        return 'mjd'

    # @property
    # def time_ref(self):
    #     """Time reference (`~astropy.time.Time`)."""
    #     return time_ref_from_dict(self.table.meta)

    def _make_time(self, colname):
        val = self.table[colname].data
        scale = self.time_scale
        format = self.time_format
        return Time(val, scale=scale, format=format)

    @property
    def time(self):
        """Time (`~astropy.time.Time`)."""
        return self._make_time('time')

    @property
    def time_min(self):
        """Time bin start (`~astropy.time.Time`)."""
        return self._make_time('time_min')

    @property
    def time_max(self):
        """Time bin end (`~astropy.time.Time`)."""
        return self._make_time('time_max')

    @property
    def time_mid(self):
        """Time bin center (`~astropy.time.Time`).

        ::
            time_mid = time_min + 0.5 * time_delta
        """
        return self.time_min + 0.5 * self.time_delta

    @property
    def time_delta(self):
        """Time bin width (`~astropy.time.TimeDelta`).

        ::
            time_delta = time_max - time_min
        """
        return self.time_max - self.time_min

    @classmethod
    def read(cls, filename, **kwargs):
        """Read from file.

        Parameters
        ----------
        filename : str
            Filename
        kwargs : dict
            Keyword arguments passed to `astropy.table.Table.read`.
        """
        filename = make_path(filename)
        table = Table.read(str(filename), **kwargs)
        return cls(table=table)

    def write(self, filename, **kwargs):
        """Write to file.

        Parameters
        ----------
        filename : str
            Filename
        kwargs : dict
            Keyword arguments passed to `astropy.table.Table.write`.
        """
        filename = make_path(filename)
        self.table.write(str(filename), **kwargs)

    def compute_fvar(self):
        r"""Calculate the fractional excess variance.

        This method accesses the the ``FLUX`` and ``FLUX_ERR`` columns
        from the lightcurve data.

        The fractional excess variance :math:`F_{var}`, an intrinsic
        variability estimator, is given by

        .. math::
            F_{var} = \sqrt{\frac{S^{2} - \bar{\sigma^{2}}}{\bar{x}^{2}}}.

        It is the excess variance after accounting for the measurement errors
        on the light curve :math:`\sigma`. :math:`S` is the variance.

        Returns
        -------
        fvar, fvar_err : `~numpy.ndarray`
            Fractional excess variance.

        References
        ----------
        .. [Vaughan2003] "On characterizing the variability properties of X-ray light
           curves from active galaxies", Vaughan et al. (2003)
           http://adsabs.harvard.edu/abs/2003MNRAS.345.1271V
        """
        flux = self.table['flux'].data.astype('float64')
        flux_err = self.table['flux_err'].data.astype('float64')

        flux_mean = np.mean(flux)
        n_points = len(flux)

        s_square = np.sum((flux - flux_mean) ** 2) / (n_points - 1)
        sig_square = np.nansum(flux_err ** 2) / n_points
        fvar = np.sqrt(np.abs(s_square - sig_square)) / flux_mean

        sigxserr_a = np.sqrt(2 / n_points) * (sig_square / flux_mean) ** 2
        sigxserr_b = np.sqrt(sig_square / n_points) * (2 * fvar / flux_mean)
        sigxserr = np.sqrt(sigxserr_a ** 2 + sigxserr_b ** 2)
        fvar_err = sigxserr / (2 * fvar)

        return fvar, fvar_err

    def compute_chisq(self):
        """Calculate the chi-square test for `LightCurve`.

        Chisquare test is a variability estimator. It computes
        deviations from the expected value here mean value

        Returns
        -------
        ChiSq, P-value : tuple of float or `~numpy.ndarray`
            Tuple of Chi-square and P-value
        """
        import scipy.stats as stats
        flux = self.table['flux']
        yexp = np.mean(flux)
        yobs = flux.data
        chi2, pval = stats.chisquare(yobs, yexp)
        return chi2, pval

    def plot(self, ax=None):
        """Plot flux versus time.

        Parameters
        ----------
        ax : `~matplotlib.axes.Axes` or None, optional.
            The `~matplotlib.axes.Axes` object to be drawn on.
            If None, uses the current `~matplotlib.axes.Axes`.

        Returns
        -------
        ax : `~matplotlib.axes.Axes` or None, optional.
            The `~matplotlib.axes.Axes` object to be drawn on.
            If None, uses the current `~matplotlib.axes.Axes`.
        """
        import matplotlib.pyplot as plt
        ax = plt.gca() if ax is None else ax

        # TODO: Should we plot with normal time axis labels (ISO, not MJD)?

        x, xerr = self._get_plot_x()
        y, yerr = self._get_plot_y()

        ax.errorbar(x=x, y=y, xerr=xerr, yerr=yerr, linestyle="None")
        ax.scatter(x=x, y=y)
        ax.set_xlabel("Time (MJD)")
        ax.set_ylabel("Flux (cm-2 s-1)")

        return ax

    def _get_plot_x(self):
        try:
            x = self.time.mjd
        except KeyError:
            x = self.time_mid.mjd

        try:
            xerr = x - self.time_min.mjd, self.time_max.mjd - x
        except KeyError:
            xerr = None

        return x, xerr

    def _get_plot_y(self):
        y = self.table['flux'].quantity.to('cm-2 s-1').value

        if 'flux_errp' in self.table.colnames:
            yp = self.table['flux_errp'].quantity.to('cm-2 s-1').value
            yn = self.table['flux_errn'].quantity.to('cm-2 s-1').value
            yerr = yn, yp
        elif 'flux_err' in self.table.colnames:
            yerr = self.table['flux_err'].quantity.to('cm-2 s-1').value
        else:
            yerr = None

        return y, yerr


class LightCurveEstimator(object):
    """Light curve estimator.

    For a usage example see :gp-extra-notebook:`light_curve`.

    Parameters
    ----------
    spec_extract : `~gammapy.spectrum.SpectrumExtraction`
       Contains statistics, IRF and event lists
    """

    def __init__(self, spec_extract):
        self.obs_list = spec_extract.obs_list
        self.obs_spec = spec_extract.observations
        self.off_evt_list = self._get_off_evt_list(spec_extract)
        self.on_evt_list = self._get_on_evt_list(spec_extract)

    @staticmethod
    def _get_off_evt_list(spec_extract):
        """
        Returns list of OFF events for each observations
        """
        off_evt_list = []
        for bg in spec_extract.bkg_estimate:
            off_evt_list.append(bg.off_events)
        return off_evt_list

    @staticmethod
    def _get_on_evt_list(spec_extract):
        """
        Returns list of ON events for each observations
        """
        on_evt_list = []
        for obs in spec_extract.bkg_estimate:
            on_evt_list.append(obs.on_events)

        return on_evt_list

    def create_fixed_time_bin_lc(self, time_step, spectral_model, energy_range, spectrum_extraction):
        """
        Create time intervals of fixed size and then a light curve using those intervals
        Parameters
        ----------
        time_step: float
            Size of the light curve bins
        :param spectral_model:`~gammapy.spectrum.models.SpectralModel`
            Spectral model
        :param energy_range:`~astropy.units.Quantity`
            True energy range to evaluate integrated flux (true energy)
        :param spectrum_extraction:`~gammapy.spectrum.SpectrumExtraction`
       Contains statistics, IRF and event lists
        :return:`~gammapy.time.LightCurve`
            Light curve
        """
        intervals = []
        time_start = Time(100000, format="mjd")
        time_end = Time(0, format="mjd")

        for obs in spectrum_extraction.obs_list:
            if time_start > obs.events.time.min():
                time_start = obs.events.time.min()
            if time_end < obs.events.time.max():
                time_end = obs.events.time.max()
        time = time_start.value
        while time < time_end.value:
            time += time_step
            intervals.append([Time(time - time_step, format="mjd"), Time(time, format="mjd")])

        lc = self.light_curve(
            time_intervals=intervals,
            spectral_model=spectral_model,
            energy_range=energy_range,
        )
        return lc

    def create_fixed_significance_bin_lc_v2(self, significance, significance_method, energy_range,
                                            spectrum_extraction, separators=[]):
        """
        Create time intervals such that each bin of a light curve reach a given significance
        ### To Do : check logic ###
        Parameters
        ----------
        significance: float
            Target significance for each light curve point
        significance_method:
            Select the method used to compute the significance ### unused yet ###
        energy_range:`~astropy.units.Quantity`
            True energy range to evaluate integrated flux (true energy)
        spectrum_extraction : `~gammapy.spectrum.SpectrumExtraction`
            Contains statistics, IRF and event lists
        separators : `list` of float
            Contains a list of time (in MJD) to break the current point creation
        Returns
        -------
        intervals : `list` of `~astropy.time.Time`
            List of time intervals

        """

        def gettime(item):
            return item[0]

        def in_list(item, L):
            o, j = np.where(L == item)
            for index in o:
                # yield L.index(i)
                yield index

        time_holder = []
        obs_properties = []
        n_obs = 0

        for time in separators:
            time_holder.append([time, 'break'])

        for obs in spectrum_extraction.obs_list:
            time_holder.append([obs.events.time.min().value - 0.0000001, 'start'])
            time_holder.append([obs.events.time.max().value + 0.0000001, 'end'])
            obs_properties.append([obs.observation_dead_time_fraction, spectrum_extraction.bkg_estimate[n_obs].a_off])
            n_obs += 1

        for t_index, obs in enumerate(self.obs_list):
            spec = self.obs_spec[t_index]
            # get ON and OFF evt list
            off_evt = self.off_evt_list[t_index]
            on_evt = self.on_evt_list[t_index]
            # prepare energy range filter
            e_reco = spec.e_reco
            emin = e_reco[e_reco.searchsorted(max(spec.lo_threshold, energy_range[0]))]
            emax = e_reco[e_reco.searchsorted(min(spec.hi_threshold, energy_range[1])) - 1]
            # filter ON events
            on = on_evt.select_energy([emin, emax])
            on = on.select_energy(energy_range)
            # filter OFF events
            off = off_evt.select_energy([emin, emax])
            off = off.select_energy(energy_range)

            for time in on.time:
                time_holder.append([time.value, 'on'])
            for time in off.time:
                time_holder.append([time.value, 'off'])

        # sort all elements in the table by time
        time_holder = sorted(time_holder, key=gettime)
        print(time_holder)
        time_holder = np.asarray(time_holder)
        intervals = []
        istart = 1
        i = 1
        n = 0
        count = 0
        while (time_holder[i][0] < time_holder[-1][0]):
            i += 1
            if time_holder[i][1] == 'break':
                n += np.sum(time_holder[istart:i + 1] == 'end')
                istart = i + 1
                i = istart
                print("break")
                continue
            if time_holder[i][1] != 'on' and time_holder[i][1] != 'off':
                continue

            # make a function to compute alpha
            alpha = 0
            tmp = 0
            time = 0
            xm1 = istart
            for x in in_list('end', time_holder[istart:i + 1]):
                if tmp == 0:
                    alpha += (1 - obs_properties[n][0]) * (float(time_holder[x][0]) - float(time_holder[istart][0])) * \
                             obs_properties[n][1]
                    time += (1 - obs_properties[n][0]) * (float(time_holder[x][0]) - float(time_holder[istart][0]))
                else:
                    alpha += (1 - obs_properties[n + tmp][0]) * (
                                float(time_holder[x][0]) - float(time_holder[xm1][0])) * obs_properties[n][1]
                    time += (1 - obs_properties[n + tmp][0]) * (float(time_holder[x][0]) - float(time_holder[xm1][0]))
                xm1 = x
                tmp += 1
            alpha += (1 - obs_properties[n + tmp][0]) * (float(time_holder[i][0]) - float(time_holder[xm1][0])) * \
                     obs_properties[n][1]
            time += (1 - obs_properties[n + tmp][0]) * (float(time_holder[i][0]) - float(time_holder[xm1][0]))
            alpha = time / alpha
            non = np.sum(time_holder[istart:i + 1] == 'on')
            noff = np.sum(time_holder[istart:i + 1] == 'off')
            ### add try catch
            if non == 0 or noff == 0:
                continue
            if significance_on_off(non, noff, alpha, method=significance_method) > significance:
                count += 1
                print(non, noff, alpha, significance_on_off(non, noff, alpha, method=significance_method))
                print(time_holder[istart:i + 1])
                n += np.sum(time_holder[istart:i + 1] == 'end')
                intervals.append(
                    [Time((float(time_holder[istart][0]) + float(time_holder[istart][0])) / 2, format="mjd"),
                     Time((float(time_holder[i][0]) + float(time_holder[i + 1][0])) / 2, format="mjd")])
                while time_holder[i + 1][1] != 'on' and time_holder[i + 1][1] != 'off':
                    i += 1
                istart = i + 1
                i = istart
        print(count)

        return intervals

    def create_fixed_significance_bin_lc(self, significance, significance_method, spectral_model
                                         , energy_range, spectrum_extraction):
        """
        Create time intervals and a light curve using those intervals such that each bin reach a given significance
        ### To Do : add dead time separator to force the start of new point? ###
        ### To Do : check logic ###

        :param significance: float
            Target significance for each light curve point
        :param significance_method:
            Select the method used to compute the significance ### unused yet ###
        :param spectral_model: `~gammapy.spectrum.models.SpectralModel`
            Spectral model
        :param energy_range: `~astropy.units.Quantity`
            True energy range to evaluate integrated flux (true energy)
        :param spectrum_extraction: `~gammapy.spectrum.SpectrumExtraction`
       Contains statistics, IRF and event lists
        :return: `~gammapy.time.LightCurve`
            Light curve
        """

        a_off = []
        on_event = self.on_evt_list
        on_event_times = []
        nb_obs = 0
        nb_obs_off = 0
        n_events_on = []
        for obs in on_event:
            on_event_times.append(obs.time.value)
            n_events_on.append(len(on_event_times[nb_obs]))
            nb_obs += 1
            on_event_times[nb_obs - 1].sort()
        off_event = self.off_evt_list
        off_event_times = []
        n_events_off = []
        for obs in off_event:
            off_event_times.append(obs.time.value)
            n_events_off.append(len(off_event_times[nb_obs_off]))
            nb_obs_off += 1
            off_event_times[nb_obs_off - 1].sort()
        for obs in spectrum_extraction.bkg_estimate:
            a_off.append(obs.a_off)

        intervals = []
        n = 0
        i_on = 0
        i_off = 0
        non = 0
        noff = 0
        interval_start = min(off_event_times[0][0], on_event_times[0][0])
        nstart = n
        i_on_start = i_on
        i_off_start = i_off

        if i_on < n_events_on[n]:
            if i_off < n_events_off[n]:
                if off_event_times[n][i_off] > on_event_times[n][i_on]:
                    non += 1
                    i_on += 1
                    on_start = True
                    on_end = True
                else:
                    noff += 1
                    i_off += 1
                    on_start = False
                    on_end = False
            else:
                non += 1
                i_on += 1
                on_start = True
                on_end = True
        elif i_off < n_events_off[n]:
            noff += 1
            i_off += 1
            on_start = False
            on_end = False
        else:
            print("error, no event in first observation\n")
            n = nb_obs

        while n < nb_obs:
            if i_on < n_events_on[n]:
                if i_off < n_events_off[n]:
                    if off_event_times[n][i_off] > on_event_times[n][i_on]:
                        non += 1
                        i_on += 1
                        on_end = True
                    else:
                        noff += 1
                        i_off += 1
                        on_end = False
                else:
                    non += 1
                    i_on += 1
                    on_end = True
            elif i_off < n_events_off[n]:
                noff += 1
                i_off += 1
                on_end = False
            else:
                n += 1
                i_on = 0
                i_off = 0
                if n == nb_obs:
                    continue

            if nstart != n:
                n_it = nstart + 1
                alpha = 0
                t_alpha = 0

                if off_event_times[nstart][n_events_off[nstart] - 1] > on_event_times[nstart][n_events_on[nstart] - 1]:
                    if on_start:
                        alpha += a_off[nstart] * (
                                off_event_times[nstart][n_events_off[nstart] - 1] - on_event_times[nstart][i_on_start])
                        t_alpha += off_event_times[nstart][n_events_off[nstart] - 1] - on_event_times[nstart][
                            i_on_start]
                    else:
                        alpha += a_off[nstart] * (
                                off_event_times[nstart][n_events_off[nstart] - 1] - off_event_times[nstart][
                            i_off_start])
                        t_alpha += off_event_times[nstart][n_events_off[nstart] - 1] - off_event_times[nstart][
                            i_off_start]
                else:
                    if on_start:
                        alpha += a_off[nstart] * (
                                on_event_times[nstart][n_events_off[nstart] - 1] - on_event_times[nstart][i_on_start])
                        t_alpha += on_event_times[nstart][n_events_on[nstart] - 1] - on_event_times[nstart][i_on_start]
                    else:
                        alpha += a_off[nstart] * (
                                on_event_times[nstart][n_events_off[nstart] - 1] - off_event_times[nstart][i_off_start])
                        t_alpha += on_event_times[nstart][n_events_on[nstart] - 1] - off_event_times[nstart][
                            i_off_start]
                while n_it < n:
                    if off_event_times[n_it][n_events_off[n_it] - 1] > on_event_times[n_it][n_events_on[n_it] - 1]:
                        if off_event_times[n_it][0] > on_event_times[n_it][0]:
                            alpha += a_off[n_it] * (
                                    off_event_times[n_it][n_events_off[n_it] - 1] - on_event_times[n_it][0])
                            t_alpha += off_event_times[n_it][n_events_off[n_it] - 1] - on_event_times[n_it][0]
                        else:
                            alpha += a_off[n_it] * (
                                    off_event_times[n_it][n_events_off[n_it] - 1] - off_event_times[n_it][0])
                            t_alpha += off_event_times[n_it][n_events_off[n_it] - 1] - off_event_times[n_it][0]
                    else:
                        if off_event_times[n_it][0] > on_event_times[n_it][0]:
                            alpha += a_off[n_it] * (
                                    on_event_times[n_it][n_events_on[n_it] - 1] - on_event_times[n_it][0])
                            t_alpha += on_event_times[n_it][n_events_on[n_it] - 1] - on_event_times[n_it][0]
                        else:
                            alpha += a_off[n_it] * (
                                    on_event_times[n_it][n_events_on[n_it] - 1] - off_event_times[n_it][0])
                            t_alpha += on_event_times[n_it][n_events_on[n_it] - 1] - off_event_times[n_it][0]
                    n_it += 1
                if on_end:
                    if off_event_times[n][0] > on_event_times[n][0]:
                        alpha += a_off[n] * (on_event_times[n][i_on - 1] - on_event_times[n][0])
                        t_alpha += on_event_times[n][i_on - 1] - on_event_times[n][0]
                    else:
                        alpha += a_off[n] * (on_event_times[n][i_on - 1] - off_event_times[n][0])
                        t_alpha += on_event_times[n][i_on - 1] - off_event_times[n][0]
                else:
                    if off_event_times[n][0] > on_event_times[n][0]:
                        alpha += a_off[n] * (off_event_times[n][i_off - 1] - on_event_times[n][0])
                        t_alpha += off_event_times[n][i_off - 1] - on_event_times[n][0]
                    else:
                        alpha += a_off[n] * (off_event_times[n][i_off - 1] - off_event_times[n][0])
                        t_alpha += off_event_times[n][i_off - 1] - off_event_times[n][0]
                alpha = t_alpha / alpha
            else:
                alpha = 1 / a_off[n]

            if on_end:
                interval_end = on_event_times[n][i_on - 1]
            else:
                interval_end = off_event_times[n][i_off - 1]

            if significance_on_off(non, noff, alpha, significance_method) > significance:
                intervals.append([Time(interval_start, format="mjd"), Time(interval_end, format="mjd")])

                if i_on < n_events_on[n]:
                    if i_off < n_events_off[n]:
                        if off_event_times[n][i_off] > on_event_times[n][i_on]:
                            interval_start = on_event_times[n][i_on]
                            i_on_start = i_on
                            non += 1
                            i_on += 1
                            on_start = True
                            on_end = True

                        else:
                            interval_start = off_event_times[n][i_off]
                            i_off_start = i_off
                            noff += 1
                            i_off += 1
                            on_start = False
                            on_end = False
                    else:
                        interval_start = on_event_times[n][i_on]
                        i_on_start = i_on
                        non += 1
                        i_on += 1
                        on_start = True
                        on_end = True
                elif i_off < n_events_off[n]:
                    interval_start = off_event_times[n][i_off]
                    i_off_start = i_off
                    noff += 1
                    i_off += 1
                    on_start = False
                    on_end = False
                else:
                    n += 1
                    i_on = 0
                    i_off = 0
                nstart = n
                non = 0
                noff = 0

        return intervals

    def light_curve(self, time_intervals, spectral_model, energy_range):
        """Compute light curve.

        Implementation follows what is done in:
        http://adsabs.harvard.edu/abs/2010A%26A...520A..83H.

        To be discussed: assumption that threshold energy in the
        same in reco and true energy.

        Parameters
        ----------
        time_intervals : `list` of `~astropy.time.Time`
            List of time intervals
        spectral_model : `~gammapy.spectrum.models.SpectralModel`
            Spectral model
        energy_range : `~astropy.units.Quantity`
            True energy range to evaluate integrated flux (true energy)

        Returns
        -------
        lc : `~gammapy.time.LightCurve`
            Light curve
        """
        rows = []
        for time_interval in time_intervals:
            useinterval, row = self.compute_flux_point(time_interval, spectral_model, energy_range)
            if useinterval:
                rows.append(row)

        return self._make_lc_from_row_data(rows)

    @staticmethod
    def _make_lc_from_row_data(rows):
        table = Table()
        table['time_min'] = [_['time_min'].value for _ in rows]
        table['time_max'] = [_['time_max'].value for _ in rows]

        table['flux'] = [_['flux'].value for _ in rows] * u.Unit('1 / (s cm2)')
        table['flux_err'] = [_['flux_err'].value for _ in rows] * u.Unit('1 / (s cm2)')

        table['livetime'] = [_['livetime'].value for _ in rows] * u.s
        table['n_on'] = [_['n_on'] for _ in rows]
        table['n_off'] = [_['n_off'] for _ in rows]
        table['alpha'] = [_['alpha'] for _ in rows]
        table['measured_excess'] = [_['measured_excess'] for _ in rows]
        table['expected_excess'] = [_['expected_excess'].value for _ in rows]

        return LightCurve(table)

    def compute_flux_point(self, time_interval, spectral_model, energy_range):
        """Compute one flux point for one time interval.

        Parameters
        ----------
        time_interval : `~astropy.time.Time`
            Time interval (2-element array, or a tuple of Time objects)
        spectral_model : `~gammapy.spectrum.models.SpectralModel`
            Spectral model
        energy_range : `~astropy.units.Quantity`
            True energy range to evaluate integrated flux (true energy)

        Returns
        -------
        useinterval : bool
            Is True if the time_interval produce a valid flux point
        measurements : dict
            Dictionary with flux point measurement in the time interval
        """
        tmin, tmax = time_interval[0], time_interval[1]
        livetime = 0
        alpha_mean = 0.
        alpha_mean_backup = 0.
        measured_excess = 0
        predicted_excess = 0
        n_on = 0
        n_off = 0
        useinterval = False

        # Loop on observations
        for t_index, obs in enumerate(self.obs_list):

            spec = self.obs_spec[t_index]

            # discard observations not matching the time interval
            obs_start = obs.events.time[0]
            obs_stop = obs.events.time[-1]
            if (tmin < obs_start and tmax < obs_start) or (tmin > obs_stop):
                continue

            useinterval = True
            # get ON and OFF evt list
            off_evt = self.off_evt_list[t_index]
            on_evt = self.on_evt_list[t_index]

            # introduce the e_reco binning here, since it's also used
            # in the calculation of predicted counts
            e_reco = spec.e_reco
            emin = e_reco[e_reco.searchsorted(max(spec.lo_threshold, energy_range[0]))]
            emax = e_reco[e_reco.searchsorted(min(spec.hi_threshold, energy_range[1])) - 1]

            # compute ON events
            on = on_evt.select_energy([emin, emax])
            on = on.select_energy(energy_range)
            on = on.select_time([tmin, tmax])
            n_on_obs = len(on.table)

            # compute OFF events
            off = off_evt.select_energy([emin, emax])
            off = off.select_energy(energy_range)
            off = off.select_time([tmin, tmax])
            n_off_obs = len(off.table)

            # compute effective livetime (for the interval)
            if tmin >= obs_start and tmax <= obs_stop:
                # interval included in obs
                livetime_to_add = (tmax - tmin).to('s')
            elif tmin >= obs_start and tmax >= obs_stop:
                # interval min above tstart from obs
                livetime_to_add = (obs_stop - tmin).to('s')
            elif tmin <= obs_start and tmax <= obs_stop:
                # interval min below tstart from obs
                livetime_to_add = (tmax - obs_start).to('s')
            elif tmin <= obs_start and tmax >= obs_stop:
                # obs included in interval
                livetime_to_add = (obs_stop - obs_start).to('s')
            else:
                livetime_to_add = 0 * u.sec

            # Take into account dead time
            livetime_to_add *= (1. - obs.observation_dead_time_fraction)

            # Compute excess
            obs_measured_excess = n_on_obs - spec.alpha * n_off_obs

            # Compute the expected excess in the range given by the user
            # but must respect the energy threshold of the observation
            # (to match the energy range of the measured excess)
            # We use the effective livetime and the right energy threshold
            e_idx = np.where(np.logical_and.reduce(
                (e_reco >= spec.lo_threshold,  # threshold
                 e_reco <= spec.hi_threshold,  # threshold
                 e_reco >= energy_range[0],  # user
                 e_reco <= energy_range[-1])  # user
            ))
            counts_predictor = CountsPredictor(
                livetime=livetime_to_add,
                aeff=spec.aeff,
                edisp=spec.edisp,
                model=spectral_model
            )
            counts_predictor.run()
            counts_predicted_excess = counts_predictor.npred.data.data[e_idx[:-1]]

            obs_predicted_excess = np.sum(counts_predicted_excess)

            # compute effective normalisation between ON/OFF (for the interval)
            livetime += livetime_to_add
            alpha_mean += spec.alpha * n_off_obs
            alpha_mean_backup += spec.alpha * livetime_to_add
            measured_excess += obs_measured_excess
            predicted_excess += obs_predicted_excess
            n_on += n_on_obs
            n_off += n_off_obs

        # Fill time interval information
        if useinterval:
            int_flux = spectral_model.integral(energy_range[0], energy_range[1])

            if n_off > 0.:
                alpha_mean /= n_off
            if livetime > 0.:
                alpha_mean_backup /= livetime
            if alpha_mean == 0.:  # use backup if necessary
                alpha_mean = alpha_mean_backup

            flux = measured_excess / predicted_excess.value
            flux *= int_flux
            flux_err = int_flux / predicted_excess.value
            # Gaussian errors, TODO: should be improved
            flux_err *= excess_error(n_on=n_on, n_off=n_off, alpha=alpha_mean)
        else:
            flux = 0
            flux_err = 0

        # Store measurements in a dict and return that
        return useinterval, OrderedDict([
            ('time_min', Time(tmin, format='mjd')),
            ('time_max', Time(tmax, format='mjd')),
            ('flux', flux * u.Unit('1 / (s cm2)')),
            ('flux_err', flux_err * u.Unit('1 / (s cm2)')),

            ('livetime', livetime * u.s),
            ('alpha', alpha_mean),
            ('n_on', n_on),
            ('n_off', n_off),
            ('measured_excess', measured_excess),
            ('expected_excess', predicted_excess),
        ])
