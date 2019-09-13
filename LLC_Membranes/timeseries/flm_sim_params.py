#!/usr/bin/env python

import numpy as np
from scipy.interpolate import LinearNDInterpolator
from scipy.optimize import minimize
import pandas as pd
import os


class HurstCorrection:

    def __init__(self, data_pickle='hurst_correction.pl'):
        """ When simulating fractional levy motion, the value of H passed to the algorithm does not necessarily lead to
        the H value one would infer from the correlation structure. This class can be used to tell you the value of H to
        feed to the fractional levy motion algorithm in order to achieve a specific value of the autocorrelation
        function at a lag time of 1 time step.

        :param data_pickle: database with h values

        :type data_pickle: str
        """

        script_location = os.path.realpath(os.path.join(os.getcwd(), os.path.dirname(__file__)))
        df = pd.read_pickle('%s/%s' % (script_location, data_pickle))

        H = np.array(df['H'], dtype=float)  # H values passed to FLM algorithm
        alpha = np.array(df['alpha'], dtype=float)  # alpha values passed to FLM algorithm
        h = np.array(df['h'], dtype=float)  # h value calculated based on resultant autocorrelation function

        self.interpolator = LinearNDInterpolator((h, alpha), H)  # interpolator for unstructured data

    def interpolate(self, hurst, alpha):
        """ Interpolate database values

        :param hurst: desired hurst parameter
        :param alpha: L\'evy index

        :type hurst: float
        :type alpha: float

        :return: h to plug into
        """

        interp = self.interpolator(hurst, alpha) + (1 / alpha) - 0.5

        if np.isnan(interp):
            if hurst < 1e-5:  # if desired Hurst parameter is near zero. 1e-5 is kind of arbitrary
                interp = -10  # H needs to be a large negative number to get the lag 1 acf to be close to -0.5
            else:
                raise Exception('The database is incomplete around H = %f so it cannot be interpolated. Please '
                                'add more data where this value of H has been achieved.' % hurst)

        return interp


def max_realization(input_limit, actual_limit, n):

    from LLC_Membranes.timeseries.fractional_levy_motion import FLM

    flm = FLM(0.4, 1.75, M=4, N=n)
    # short traj with lots of realizations faster than single long traj
    flm.generate_realizations(100, truncate=input_limit)

    return np.abs(flm.noise.max() - actual_limit)


def truncate(limit, n=2**10):
    """ Determine where to truncate the initial Levy distribution in fractional_levy_motion.FLM() so that the magnitude
    of draws stay below some some limit.

    :param limit:

    :type limit: float

    :return:
    """

    return minimize(max_realization, np.array(limit), args=(limit, n))


if __name__ == "__main__":

    print(truncate(3))
