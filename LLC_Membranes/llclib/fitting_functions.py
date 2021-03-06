#!/usr/bin/env python

import numpy as np
from scipy.stats import expon
from LLC_Membranes.analysis import Poly_fit
import mpmath
from scipy.optimize import curve_fit, minimize
from scipy.special import gamma, erfc


def continuum_passage_time_distribution(t, x, v, D):
    """ The distribution of passage times for a particle moving in the positive x direction past some absorbing boundary

    :param t: passage times
    :param x: location of absorbing boundary (positive number)
    :param v: average velocity of particles
    :param D: diffusion constant of particles
    """

    # This is exact and allows evaluation at single time point
    # a = np.exp(-(x - t * v)**2 / (4 * D * t))  # faster but causes underflow errors
    if type(t) is np.ndarray:
        a = np.array([np.float(mpmath.exp(-(x - i * v)**2 / (4 * D * i))) for i in t])  # mpmath has arbitrary precision
    else:
        a = float(mpmath.exp(-(x - t * v)**2 / (4 * D * t)))

    b = D * (x - t * v) / (4 * (D * t) ** 1.5)
    c = v / (2 * np.sqrt(D * t))

    try:
        return -(1 / np.sqrt(np.pi)) * a * (-b - c)
    except FloatingPointError:

        if type(a) is np.ndarray:
            print(a)
            A = []
            for i in a:
                if i < 1e-300:
                    A.append(0)
                else:
                    A.append(i)
            print(A)
            return np.array(A)
        else:
            return 0


def continuum_ptime_distribution_expected_value(t, x, v, D):
    """ Integrate this equation (scipy.integrate.quad) to get the expected value

    :param t:
    :param x:
    :param v:
    :param D:
    :return:
    """

    return t * continuum_passage_time_distribution(t, x, v, D)


def stretched_exponential(x, A, beta):

    #logy = -x ** beta + np.log(A)
    #return np.exp(logy)
    return A * np.exp(-x ** beta)


def stretched_exponential_expected_value(x, A, beta, area):
    """ For integrating in order to calculate the expected value

    E[x] = integrate xf(x) dx

    :param x:
    :param A:
    :param beta:
    :return:
    """

    return x * A * np.exp(-x ** beta) / area


def log_power_law(x, a, alpha):
    """ Log power law dependence of MSD curve (for curve fitting)

    :param x: independent variable values
    :param a: scaling coefficient
    :param alpha: power (< 1: subdiffusive, 1: brownian, >1: superdiffusive)

    :return function evaluated at x values
    """

    return a + alpha * np.log(x)


def power_law(x, a, alpha):
    """ Power law dependence of MSD curve

    :param x: independent variable values
    :param a: scaling coefficient
    :param alpha: power (< 1: subdiffusive, 1: brownian, >1: superdiffusive)

    :return function evaluated at x values
    """

    return a * x ** alpha


def cdf_exp(left, right, scale):
    """ Calculate area under expnonential curve between two x locations """
    return expon.cdf(right, scale=scale) - expon.cdf(left, scale=scale)


def exponential_integrated(edges, A, B):
    """ Fit bins to exponential function based on integrated area under bins. The form of the exponential is: A*B*e^-Bx

    :param edges: edges of bins
    :param A: exponential function parameter. Controls scale of exponential function
    :param B: exponential function parameter. Controls rate of decay

    :type edges: np.ndarray
    :type A: float
    :type B: float

    :return integrated area between bin edges and under exponential curve as a function of x
    """

    bin_width = edges[1] - edges[0]  # bin width

    pdf = []
    for i in range(len(edges) - 1):
        pdf.append(cdf_exp(edges[i], edges[i + 1], 1/float(B)))

    pdf.append(1 - cdf_exp(0, edges[-1], 1/float(B)))

    return np.array(pdf) * (float(A) / bin_width)


def cdf_power_law(left, right, scale, alpha):
    """ Calculate area under power law curve between two x locations by integrating scale*e^-alpha from left to right.

    Scipy does not have tabulated data for power laws with alpha < 1, so I wrote my own for that case

    :param left: left bound of integral
    :param right: right bound of integral
    :param scale: parameter of power law function that controls its scale
    :param alpha: exponent of power law

    :type left: float
    :type right: float
    :type scale: float
    :type alpha: float

    :return: integrated area between left and right
    :rtype: float
    """

    exponent = 1 - alpha

    return (scale / exponent) * (right ** exponent - left ** exponent)


def powerlaw_integrated(edges, alpha, A):
    """ Fit bins to powerlaw function of form: At^-alpha

    :param edges: edges of bins to which power law will be fit
    :param alpha: exponent of power law
    :param A: parameter of power law function that controls its scale

    :type edges: np.ndarray
    :type alpha: float
    :type A: float

    :return: integrated area between bin edges and under power law curve as function of x
    """

    pdf = []
    for i in range(len(edges) - 1):
        pdf.append(cdf_power_law(edges[i], edges[i + 1], A, alpha))

    pdf.append(cdf_power_law(edges[-1], np.inf, A, alpha))

    return np.array(pdf) / (edges[1] - edges[0])


def fit_power_law(x, y, cut=1, interactive=True):
    """ Fit power law to MSD curves
    TODO: weighted fit (need to do error analysis first)

    :param y: y-axis values of MSD curve (x-axis values are values from self.time_uniform
    :param cut: fraction of trajectory to include in fit

    :type y: np.ndarray
    :type cut: float

    :return: Coefficient and exponent in power law of form [coefficient, power]
    """

    end = int(cut * len(x))  # fit up until a fraction, cut, of the trajectory

    nonzero = y.nonzero()

    # fit line to linear log plot
    A = Poly_fit.poly_fit(np.log(x[nonzero]), np.log(y[nonzero]), 1)[-1]

    return [np.exp(A[0]), A[1]]


def line(m, x, b):
    """ Return y values of a line given points, a slope and an intercept

    y = m*x + b

    :param m: slope
    :param x: points
    :param b: y-intercept

    :type m: float
    :type x: point or array of points
    :type b: float

    :return: y or array of y-values
    :rtype: np.ndarray()

    """

    return m * x + b


def zeta(x, alpha):
    """ Numerical estimate of Hurwitz zeta function. Used as discrete power law
    distribution normalization constant

    sum from n=0 to infinity of (n + x)^-\alpha

    :param x: point at which to evaluate function
    :param alpha: exponent of power law
    :param upper_limit: number of terms used to calculate Hurwitz zeta function (highest value of n above)

    :type x: float
    :type alpha: float
    :type upper_limit: int

    :return: evaluation of Hurwitz zeta function at x
    :rtype: float
    """

    if type(alpha) is np.ndarray:
        alpha = alpha[0]

    return float(mpmath.zeta(alpha, a=x))


def power_law_discrete_log_likelihood(alpha, x, xmin, minimize=False):
    """ Calculate log likelihood for alpha given a set of x values that might come from a
    power law distribution

    :param alpha: power law exponent. Calculates the log-likelihood of this value of alpha
    for the data
    :param x: array of values making up emperical distribution
    :param xmin: lower bound of power law distribution
    :param upper_limit: number of terms used to calculate zeta function

    :type alpha: float
    :type x: np.ndarray
    :type xmin: float
    :type upper_limit: int

    :return log-likelihood of input parameters
    :rtype float
    """

    n = x.size
    z = zeta(xmin, alpha)

    res = - n * np.log(z) - alpha * sum([np.log(i) for i in x])

    if minimize:
        return res * - 1
    else:
        return res


def gaussian_log_likelihood(parameters, data, maximize=False):
    """ Calculate log-likelihood given parameters and data

    :param parameters: a tuple of form (mean, sigma)
    :param data: data that might be gaussian
    :param maximize: if this is true, the opposite sign of the log-likelihood is returned so it can be used in a
    minimization function (as a way to calculate the maximum)

    :type parameters: tuple
    :type data: np.ndarray
    :type maximize: bool

    :return: log-likelihood
    """

    mean, sigma = parameters  # unpack parameters
    N = data.size

    L = -0.5 * N * np.log(2 * np.pi * sigma ** 2) - sum([((i - mean) ** 2) / (2 * sigma ** 2) for i in data])

    if maximize:
        return -L
    else:
        return L


def hurst_autocovariance(K, H):
    """ Return the analytical autocovariance of fractional gaussian noise for a given hurst exponent as a function
    of step number, k

    .. math::

        \gamma(k) = \dfrac{1}{2}[|k-1|^{2H} - 2|k|^{2H} + |k + 1|^{2H}]

    :param K: values of k at which to evaluate autocovariance (only integer values make sense)
    :param H: hurst exponent

    :return: analytical autocovariance function for fractional gaussian noise
    """

    return np.array([0.5 * (np.abs(k - 1)**(2*H) - 2 * np.abs(k)**(2*H) + np.abs(k + 1)**(2*H)) for k in K])


def powerlaw_cutoff_mle(x, xmin=1., guess=(1.5, 0.1)):
    r""" Given data, determine the maximum likelihood paramters, :math:`\alpha` and :math:`\lambda` for a power law with
    an exponential cutoff. Based on `this answer
    <https://mathoverflow.net/questions/66731/maximum-likelihood-estimator-for-power-law-with-exponential-cutoff#87969>`_
    to a mathoverflow question.

    The PDF for a power law with an exponential cut-off is:

    .. math::

        P(x; \alpha , \lambda , x_{min}) = \frac{\lambda^{1-\alpha}}{\Gamma (1-\alpha , \lambda x_{min})} x^{-\alpha}e^{-\lambda x}

    There is no closed form formula for the MLE parameters, so we maximize the log likelihood:

    .. math::

        \mathcal{L} = n*(1 - \alpha)ln\lambda - n*ln\Gamma(1 - \alpha, x_{min}\lambda) - \alpha\sum_{i=1}^{n}ln x_i - \lambda\sum_{i=1}^n x_i

    :param x: vector of data points
    :param xmin: minimum x-value where power law behavior is observed
    :param guess: initial guess at paramters (:math:`\alpha`, :math:`\lambda`)

    :type x: numpy.ndarray
    :type xmin: float
    :type guess: tuple of floats

    :return: MLE values of :math:`\alpha` and :math:`\lambda`
    """

    args = (x, xmin)

    return minimize(powerlaw_cutoff_loglikelihood, np.array(guess), args=args, bounds=[(1, 3), (1e-10, np.inf)]).x


def powerlaw_cutoff_loglikelihood(params, x, xmin=1.):
    """ Calculate log likelihood that a power law with an exponential cut-off and paramters alpha and lamb fits the
    data.

    The likelihood function is:

    .. math::

        \mathcal{L} = n*(1 - \alpha)ln\lambda - n*ln\Gamma(1 - \alpha, x_{min}\lambda) - \alpha\sum_{i=1}^{n}ln x_i - \lambda\sum_{i=1}^n x_i

    :param params: parameters of distribution in order [alpha, lambda]
    :param x: data
    :param xmin: minimum x-value where power law behavior is observed

    :type params: tuple of floats
    :type x: numpy.ndarray
    :type xmin: float

    :return: likelihood
    :rtype: float
    """

    alpha, lamb = params[0], params[1]
    a = x.size * np.log(lamb ** (1 - alpha) / float(mpmath.gammainc(1 - alpha, lamb * xmin)))
    result = a - alpha * np.log(x).sum() - lamb * x.sum()

    return -result


def powerlaw_mle(x, xmin, guess=1.5):
    r""" Return maximum likelihood estimate of parameters describing power law distribution.

    .. math::

        f(x) = Cx^{\alpha}

    :param x: data
    :param xmin: minimum x-value where power law behavior is observed
    :param guess: initial guess at alpha

    :type x: numpy.ndarray
    :type xmin: float
    :type guess: initial guess at :math:`\alpha`

    :return: MLE estimate of :math:`alpha`
    """

    args = (x, xmin, True)  # (dwell times, xmin, maximize = True)

    return minimize(power_law_discrete_log_likelihood, guess, args=args, bounds=[(1.01, 3)]).x[0]


def hurst_autocorrelation_flm(k, H):
    """ Evaluate the analytical hurst autocorrelation function:

    .. math::

        \gamma(k) = \frac{E[L(1)^2]}{2\Gamma(2H + 1)sin(\pi H)} \times [|k-1|^{2H} - 2|k|^{2H} + |k + 1|^{2H}]

    :param k: time lag values at which to calculate acf
    :param H: hurst self-similarity parameter

    :return:
    """

    autocov = np.abs(k - 1) ** (2 * H) - 2 * np.abs(k) ** (2 * H) + np.abs(k + 1) ** (2 * H)
    autocov *= 1 / (2 * gamma(2 * H + 1) * np.sin(np.pi * H))

    return autocov / autocov[0]


def fit_hurst_autocorrelation_flm(x, max_k=-1, guess=0.5):

    h_opt = curve_fit(hurst_autocorrelation_flm, np.arange(max_k + 1), x[:max_k + 1], p0=guess)[0]

    return h_opt


def exponential_plateau(x, M, a):
    """ An exponential-type plateau-ing function of the form:

    .. math::

        f(x) = M(1 - e^{-ax})

    :param x: independent variable
    :param M: plateau value
    :param a: decay rate

    :type x: numpy.ndarray
    :type M: float
    :type a: float

    :return: f evaluated at all x
    """

    return M * (1 - np.exp(-a * x))


def fit_exponential_plateau(x, y):
    """ Fit an exponential plateau function using non-linear least squares (via scipy.optimize.curve_fit)

    .. math::

        f(x) = M(1 - e^{-ax})

    :param x: independent variable
    :param y: dependent variable

    :type x: numpy.ndarray
    :type y: numpy.ndarray

    :return M: plateau value (maximum value reached)
    :return a: decay rate parameter

    :rtype M: float
    :rtype a: float
    """

    M_guess = np.max(y)
    a_guess = -((-1 / x[1:]) * (1 - (y[1:] / M_guess))).mean()  # exclude first data point since x = 0

    opt, cov = curve_fit(exponential_plateau, x, y, p0=(M_guess, a_guess))

    return opt, cov


def powerlaw_plateau(x, M, a):
    """ A power law-type plateau-ing function of the form:

    .. math::

        f(x) = M(1 - x^{-a})

    :param x: independent variable
    :param M: plateau value
    :param a: decay rate

    :type x: numpy.ndarray
    :type M: float
    :type a: float

    :return: f evaluated at all x
    """

    return M * (1 - (x**-a))


def fit_powerlaw_plateau(x, y):
    """ Fit an power law plateau function using non-linear least squares (via scipy.optimize.curve_fit)

    .. math::

        f(x) = M(1 - x^{-a})

    :param x: independent variable
    :param y: dependent variable

    :type x: numpy.ndarray
    :type y: numpy.ndarray

    :return M: plateau value (maximum value reached)
    :return a: decay rate parameter

    :rtype M: float
    :rtype a: float
    """

    M_guess = np.max(y)
    #a_guess = (-np.log(1 - (y / M_guess)) / np.log(x)).mean()
    a_guess = 1

    opt, cov = curve_fit(powerlaw_plateau, x, y, p0=(M_guess, a_guess))

    return opt, cov
