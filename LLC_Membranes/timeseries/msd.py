#! /usr/bin/env python

import os
import sys
import argparse
import numpy as np
import mdtraj as md
import matplotlib.pyplot as plt
from LLC_Membranes.analysis import Poly_fit, top
from LLC_Membranes.llclib import physical, topology, timeseries, fitting_functions, atom_props, file_rw
from statsmodels.tsa.stattools import adfuller
from scipy import stats
import tqdm
import sqlite3 as sql


def initialize():

    parser = argparse.ArgumentParser(description='Calculate mean squared displacement (MSD) and diffusion coefficient'
                                                 'for a specific residue or set of atoms in a trajectory')
    parser.add_argument('-t', '--trajectory', default='wiggle.trr', help='Path to input file')
    parser.add_argument('-g', '--gro', default='wiggle.gro', help='Name of .gro coordinate file')
    parser.add_argument('-r', '--residue', type=str, help='Name of residue whose diffusivity we want')
    parser.add_argument('-atoms', nargs='+', help='Name of atoms whose collective diffusivity is desired')
    parser.add_argument('-b', '--nboot', default=200, help='Number of bootstrap trials to be run')
    parser.add_argument('-f', '--frontfrac', default=0, type=float, help='Where to start fitting line on msd curve')
    parser.add_argument('-F', '--fracshow', default=.2, type=float, help='Percent of graph to show, also where to stop '
                        'fitting line during diffusivity calculation')
    parser.add_argument('-a', '--axis', default='z', type=str, help='Which axis to compute msd along')
    parser.add_argument('-nboot', default=200, type=int, help='Number of bootstrap trials for error estimation')
    parser.add_argument('-ensemble', '--ensemble', action="store_true", help='Calculate MSD as ensemble average')
    parser.add_argument('-compare', '--compare', action="store_true", help='Compare time-averaged and ensemble-averaged '
                                                                           'time series')
    parser.add_argument('-power_law', '--power_law', action="store_true", help='Fit MSD to a power law')

    parser.add_argument('-tails', '--tails', action="store_true", help='Only look at transport within tails')
    parser.add_argument('-pores', '--pores', action="store_true", help='Only look at transport within pores')
    parser.add_argument('-pr', '--pore_radius', default=1.5, type=float, help='Radius of pores. Anything greater than '
                        'this distance from the pore center will not be included in calculation')
    parser.add_argument('-acf', '--autocorrelation', action="store_true", help='Plot step autocorrelation function')
    parser.add_argument('-acov', '--autocovariance', action="store_true", help='Plot step autocovariance function')
    parser.add_argument('-nofit', '--nofit', action="store_true", help='Do not attempt to fit any curve to MSD')

    parser.add_argument('--update', action="store_true", help="update database with MD MSD values")
    parser.add_argument('-wt', '--wt_water', default=10, type=float, help='Weight percent water in system. Only need'
                                                                          'to adjust this if updating database.')
    parser.add_argument('-s', '--savename', default=False, type=str, help='If specified, save the MSD curve in a '
                                                                          'pickled .pl file of this name.')
    parser.add_argument('-begin', '--begin', default=0, type=int, help='First frame to analyze')
    parser.add_argument('-end', '--end', default=-1, type=int, help='Last frame to analyze')

    return parser


class Diffusivity(object):

    def __init__(self, traj, gro, axis, begin=0, end=-1, startfit=0.01, endfit=1, residue=False, atoms=(), restrict=(),
                 solute_indices=()):
        """ Calculate the mean squared displacement of a selection from an unwrapped coordinate trajectory

        :param traj: unwrapped trajectory (i.e. gmx trjconv with -pbc nojump)
        :param gro: representative coordinate file
        :param axis: axis along which to compute MSD (choices: x, y, z, xy, yz, xz, xyz)
        :param startfit: start linear fit to MSD startfit % into trajectory
        :param endfit: end linear fit to MSD endfit % into trajectory
        :param residue: if specified, the residue whose center of mass MSD will be measured
        :param atoms: if specified, group of atoms whose center of mass MSD will be measured
        :param restrict: restrict selection to certain indices. For example, if you want to calculate MSD of a certain\
        residue, but only include a fraction of the total residues in the system.
        :param solute_indices: different from restrict, this specifies the indices of the solute centers of mass of
        which to calculate the MSDs. For example, if there are 24 solutes and you only want the MSD of the first two
        solutes, you would pass [0, 1].

        :type traj: str
        :type gro: str
        :type axis: str
        :type startfit: int
        :type endfit: int
        :type residue: str
        :type atoms: list
        :type restrict: list
        :type solute_indices: list
        """

        # initialize path locations
        self.script_location = os.path.realpath(os.path.join(os.getcwd(), os.path.dirname(__file__)))
        self.top_location = "%s/../top/topologies" % self.script_location

        # initialize arguments for use in other functions
        self.gro = gro
        self.traj = traj
        self.residue = residue

        # initialize trajectory properties
        print('Loading trajectory...', end='', flush=True)
        self.t = md.load(self.traj, top=self.gro)[begin:end]  # load trajectory
        print('Done!')
        self.nT = self.t.n_frames  # number of frames
        self.time = self.t.time / 1000  # time stamp on each frame, converted to nanoseconds

        # initialize fits to data, error analysis and plotting parameters
        self.startfit = int(startfit*self.nT)  # index at which to start fit
        if endfit == 1:
            self.endfit = self.nT - 1
        else:
            self.endfit = int(endfit*self.nT)  # index at which to end fit

        self.y_fit = []
        self.A = []  # fit parameters
        self.W = []  # weight matrix for curve fitting
        self.power_law_fit = None
        self.errorevery = int(np.ceil(self.nT / 100.0))  # plot only 100 bars total
        self.confidence_interval = 0
        self.yfit = None

        # autocorrelation / autocovariance
        self.acf = None
        self.acov = None

        # initialize results
        self.MSD = None
        self.limits = None
        self.MSD_average = 0
        self.slope_error = 0
        self.Davg = 0

        if residue:

            res = residue
            if res == 'SOL':  # mdtraj changes the name from SOL to HOH
                res = 'HOH'

            if restrict:
                selection = [a.index for a in self.t.topology.atoms if a.residue.name == res and a.index in restrict]
            else:
                selection = [a.index for a in self.t.topology.atoms if a.residue.name == res]

            topol = topology.Residue(res)
            atoms_per_residue = topol.natoms  # number atoms in a single residue
            matoms = [i for i in topol.mass.values()]  # mass of the atoms in residue
            self.mres = sum(matoms)

        elif atoms:

            selection = [a.index for a in self.t.topology.atoms if a.name in atoms]

            atoms_per_residue = len(atoms)
            matoms = np.array([atom_props.mass[x] for x in atoms])
            self.mres = np.sum(matoms)
        else:
            sys.exit('Error: No valid group of atoms or residues selected')

        self.map = topology.map_atoms(selection, nres_atoms=atoms_per_residue)
        pos = self.t.xyz[:, selection, :]

        self.axis = []
        if 'x' in axis:
            self.axis.append(0)
        if 'y' in axis:
            self.axis.append(1)
        if 'z' in axis:
            self.axis.append(2)

        print('Calculating center of mass of residues...', end='', flush=True)
        self.com = np.zeros([self.nT, pos.shape[1] // atoms_per_residue, 3])  # track the center of mass of each residue

        for f in range(self.nT):
            for i in range(self.com.shape[1]):
                w = (pos[f, i * atoms_per_residue:(i + 1) * atoms_per_residue, :].T * matoms).T  # weight each atom in the residue by its mass
                self.com[f, i, :] = np.sum(w, axis=0) / self.mres  # sum the coordinates and divide by the mass of the residue
        print('Done!')

        if solute_indices:
            self.com = self.com[:, solute_indices, :]

        # for i in range(self.com.shape[1]):
        #     plt.plot(self.com[:, i, 2])
        #     plt.title('%d' % i)
        #     plt.show()
        # exit()
        # plot 'random' z coordinate traces
        # np.random.seed(4)  # 4 gives a nice spread for ethanol
        # trajs = np.random.randint(0, self.com.shape[1], size=3)
        # print(trajs)
        # trajs = np.arange(24)
        # for i in trajs:
        #     plt.plot(self.time, self.com[:, i, 2], linewidth=2)

        # plt.ylabel('$z$-coordinate (nm)', fontsize=14)
        # plt.xlabel('Time (ns)', fontsize=14)
        # plt.gcf().get_axes()[0].tick_params(labelsize=14)
        #
        # plt.tight_layout()
        #
        # plt.show()
        # exit()

        self.weights = True
        if self.com.shape[1] == 1:
            self.weights = False

        self.dt = self.time[-1] - self.time[-2]  # time step (assuming equispaced time points)

    def restrict_to_pore(self, r, dwell_fraction=0.95, tails=False, build_monomer='NAcarb11V', spline=False,
                         buffer=0, npores=4):
        """ Restrict calculations to center of masses (COMs) that primarily stay in the pore OR tail region

        :param r: radius of pore. Anything greater than r from the pore center is considered the tail region
        :param dwell_fraction: Fraction of time spent in region of interest required in order to keep trajectory
        :param tails: if True, then restrict calculations to COMs primarily in the tail region
        :param build_monomer: monomer coordinate file of which liquid crystal membrane is mode
        :param spline: track pore centers with a 3D spline
        :param buffer: Do not count molecules below _buffer_ or above z-box-vector - _buffer_

        :type r: float
        :type tails: bool
        :type build_monomer: str
        :type spline: bool
        :type buffer: float
        """

        # find pore centers
        pore_defining_atoms = topology.LC(build_monomer).pore_defining_atoms
        pore_atoms = [a.index for a in self.t.topology.atoms if a.name in pore_defining_atoms]
        if spline:
            print('Creating pore splines')
            pore_centers = physical.trace_pores(self.t.xyz[:, pore_atoms, :], self.t.unitcell_vectors, 10)
        else:
            pore_centers = physical.avg_pore_loc(npores, self.t.xyz[:, pore_atoms, :], self.t.unitcell_vectors)

        inregion = physical.partition(self.com, pore_centers, r, buffer=buffer,
                                      unitcell=self.t.unitcell_vectors, npores=npores)

        if tails:
            inregion = ~inregion  # '~' flips True and False

        dwell = np.full((self.t.n_frames, self.com.shape[1]), False, dtype=bool)

        for t in range(self.t.n_frames):
            dwell[t, inregion[t]] = True

        fraction_dwelled = np.sum(dwell, axis=0) / self.t.n_frames  # fraction of total time spend in region of interest

        keep = np.where(fraction_dwelled >= dwell_fraction)[0]

        self.com = self.com[:, keep, :]

    def calculate(self, ensemble=False):
        """ Calculate mean squared displacement of trajectory

        :param ensemble: Calculate the ensemble MSD instead of the time-averaged MSD

        :type ensemble: bool
        """

        print('Calculating MSD...', end='', flush=True)
        self.MSD = timeseries.msd(self.com, self.axis, ensemble=ensemble)
        self.MSD_average = np.mean(self.MSD, axis=1)
        print('Done!')

    def step_autocorrelation(self):
        """ Calculate autocorrelation of step length in the direction of axis.
        """

        self.acf = timeseries.step_autocorrelation(self.com, axis=self.axis)

    def fit_linear(self):
        """ Fit a line to the MSD curve. This function will prompt the user to define the linear region as there is no
        rule of thumb for determining exactly where to fit the MSD curve. Make sure to report what you chose as the
        linear region.
        """

        fit = 0
        while fit == 0:

            self.yfit, _, self.slope_error, _, A = Poly_fit.poly_fit(self.time[self.startfit:self.endfit],
                                                  self.MSD_average[self.startfit:self.endfit], 1, self.W)

            plt.plot(self.time[self.startfit:self.endfit], self.yfit, '--', color='black', label='Linear Fit')

            # plt.errorbar(self.time, self.MSD_average, yerr=[self.limits[0, :], self.limits[1, :]],
            #              errorevery=self.errorevery, label='MSD')
            plt.plot(self.time, self.MSD_average, label='MSD')

            plt.ylabel('MSD ($nm^2$)', fontsize=14)
            plt.xlabel('time (ns)', fontsize=14)
            plt.gcf().get_axes()[0].tick_params(labelsize=14)
            plt.legend(loc=2)
            plt.tight_layout()
            plt.ion()
            plt.show()
            fit = int(input("Type '1' if the fit looks good: "))
            if fit != 1:
                print('Press enter to following prompts to leave as is')
                self.startfit = float(input("Time to start fit (ns): ") or self.startfit)
                self.endfit = float(input("Time to stop fit (ns): ") or self.endfit)
                self.startfit = int(self.startfit / (self.dt))  # convert time to index in t.time
                self.endfit = int(self.endfit / (self.dt))
            plt.clf()

    def fit_power_law(self, y):
        """ Fit power law to MSD curve.

        :param y: y values of MSD curve

        :type y: numpy.ndarray

        :return: Coefficient and exponent in power low of form [coefficient, power]
        """

        A = Poly_fit.poly_fit(np.log(self.time[1:]), np.log(y[1:]), 1, self.W)[-1]

        return [np.exp(A[0]), A[1]]

    def stationarity(self, axis=2):
        """ Determine if increments (resulting from first-order differencing) are stationary using the Augmented
        Dickey Fuller test. The null hypothesis of the ADF test is that the time series has a unit root, i.e. it is
        non-stationary. If the returned p-value is below some threshold (often 0.05), then one can reject the null
        hypothesis and claim stationarity. The p-value is calculated from the ADF statistic. A more negative ADF
        statistic indicates a greater chance of rejecting the null hypothesis.

        :param axis: axis along which to measure increments. (x = 0, y = 1, z = 2)

        :type axis: int

        :return: p value for each trajectory
        :rtype: numpy.ndarray
        """

        pvalues = np.zeros([self.com.shape[1]])
        for res in range(self.com.shape[1]):
            pvalues[res] = adfuller(self.com[1:, res, axis] - self.com[:-1, res, axis])[1]

        return pvalues

    def ergodicity_breaking_parameter(self):

        mean_msd_variance = np.zeros_like(self.MSD_average)
        for i in range(1, self.MSD.shape[0]):
            mean_msd_variance[i] = np.square(self.MSD[i, :]).mean()

        mean_msd_squared = np.square(self.MSD_average)  # <delta^2>^2

        return (mean_msd_variance - mean_msd_squared) / mean_msd_squared

    def bootstrap_power_law(self, N):
        """ Bootstrap fits of a power law to the MSD

        :param N: number of bootstrap trials

        :type N: int
        """

        self.power_law_fit = np.zeros([N, 2])

        print('Bootstrapping power law fit...')
        for b in tqdm.tqdm(range(N)):

            choices = np.random.randint(0, self.MSD.shape[1], size=self.MSD.shape[1])
            bootstrapped_msd = self.MSD[:, choices].mean(axis=1)
            self.power_law_fit[b, :] = self.fit_power_law(bootstrapped_msd)

            # plt.plot(self.time/1000, bootstrapped_msd)
            # plt.plot(self.time[1:]/1000, power_law(self.time[1:], self.power_law_fit[b, 0],
            #                                        self.power_law_fit[b, 1]))
            # plt.show()

    def bootstrap(self, N, fit_line=True, confidence=68):
        """ Estimate error at each point in the MSD curve using bootstrapping

        :param N: number of bootstrap trials
        :param fit_line: Fit a line to the MSD curve
        :param confidence: percent confidence interval (out of 100)

        :type N: int
        :type fit_line: bool
        :type confidence: float

        """

        eMSDs = np.zeros([self.MSD.shape[0], N], dtype=float)  # create n bootstrapped trajectories

        print('Bootstrapping MSD curves...')
        for b in tqdm.tqdm(range(N)):
            indices = np.random.randint(0, self.com.shape[1], self.com.shape[1])  # randomly choose particles with replacement
            for n in range(self.com.shape[1]):
                eMSDs[:, b] += self.MSD[:, indices[n]]  # add the MSDs of a randomly selected particle
            eMSDs[:, b] /= self.com.shape[1]  # Divide every timestep by Nparticles -- average the MSDs

        lower_confidence = (100 - confidence) / 2
        upper_confidence = 100 - lower_confidence

        self.limits = np.zeros([2, self.MSD.shape[0]], dtype=float)  # upper and lower bounds at each point along MSD curve
        # determine error bound for each tau (out of n MSD's, use that for the error bars)
        for t in range(self.MSD.shape[0]):
            self.limits[0, t] = np.abs(np.percentile(eMSDs[t, :], lower_confidence) - self.MSD_average[t])
            self.limits[1, t] = np.abs(np.percentile(eMSDs[t, :], upper_confidence) - self.MSD_average[t])

        if fit_line:

            npts = self.endfit - self.startfit
            if self.weights:
                self.W = np.zeros((npts, npts))
                for i in range(npts):
                    self.W[i, i] = 1 / ((self.limits[0, i + self.startfit]) ** 2)
            else:
                self.W = 'none'

            slopes = np.zeros([N])
            for b in range(N):
                # fit line to each bootstrapped MSD
                A = Poly_fit.poly_fit(self.time[self.startfit:self.endfit], eMSDs[self.startfit:self.endfit, b],
                                      1, self.W)[-1]
                slopes[b] = A[1]

            slopes /= (2*100000*len(self.axis))  # nm^2 / ns = 1.0e-5 cm^2/s

            # calculate 95 % confidence interval
            self.confidence_interval = stats.t.interval(0.95, N - 1, loc=slopes.mean(), scale=stats.sem(slopes))
            self.Davg = slopes.mean()

    def plot(self, fracshow=0.5, save=False, savename='diffusivity.pdf', savedata=False, show=False):
        """ Plot the MSD curve

        :param fracshow: fraction of MSD curve to show on plot
        :param save: save plot
        :param savename: name under which to save plot (including file extension)
        :param savedata: save MSD curve data in a numpy compressed .npz file
        :param show: show plot when done

        :type fracshow: float
        :type save: bool
        :type savename: str
        :type savedata: bool
        :type show: bool
        """

        plt.figure()
        self.endfit = int(fracshow * self.time.size)

        plt.plot(self.time[:self.endfit], self.MSD_average[:self.endfit], label='MSD')
        plt.fill_between(self.time[:self.endfit], self.MSD_average[:self.endfit] + self.limits[0, :self.endfit],
                         self.MSD_average[:self.endfit] - self.limits[1, :self.endfit], alpha=0.7)

        last = self.MSD_average[(self.endfit - 1)]

        print('Final frame MSD: %.2f [%.2f, %.2f]' % (last, last - self.limits[1, self.endfit - 1],
                                                      last + self.limits[0, self.endfit - 1]))

        if savedata:

            np.savez_compressed('msd.npz', time=self.time[:self.endfit], msd=self.MSD_average[:self.endfit],
                                yerr=[self.limits[0, :self.endfit], self.limits[1, :self.endfit]])

        plt.ylabel('MSD ($nm^2$)', fontsize=14)
        plt.xlabel('time (ns)', fontsize=14)
        plt.gcf().get_axes()[0].tick_params(labelsize=14)
        plt.tight_layout()

        if save:
            plt.savefig(savename)

        if show:
            plt.show(block=True)

    def plot_power_law(self, bins=25):

        A = self.fit_power_law(self.MSD_average)

        fig, ax = plt.subplots(1, 2, figsize=(8, 4))

        ax[0].plot(self.time, self.MSD_average, linewidth=2)
        ax[0].plot(self.time, fitting_functions.power_law(self.time, A[0], A[1]), linewidth=2,
                   label=r'Power law fit to $Ae^{\alpha}$')
        ax[0].set_ylabel('Ensemble MSD ($nm^2/ns$)', fontsize=14)
        ax[0].set_xlabel('Time (ns)', fontsize=14)
        ax[0].xaxis.set_tick_params(labelsize=14)
        ax[0].yaxis.set_tick_params(labelsize=14)
        ax[0].legend()

        ax[1].hist(self.power_law_fit[:, 1], bins=bins, density=True)
        ax[1].set_title(r'Bootstrapped values of $\alpha$', fontsize=14)
        ax[1].set_ylabel('Probability', fontsize=14)
        ax[1].set_xlabel(r'$\alpha$', fontsize=14)
        ax[1].xaxis.set_tick_params(labelsize=14)

        plt.tick_params(labelsize=14)
        plt.tight_layout()
        # print(self.power_law_fit[:, 1].mean())
        # plt.savefig('/home/bcoscia/PycharmProjects/LLC_Membranes/Ben_Manuscripts/transport/figures/msd_power_law.pdf')
        # plt.show()
        # exit()

    def plot_autocorrelation(self, show=True):
        """ Plot autocorrelation function

        :param show: show plot

        :type show: bool

        :return:
        """

        plt.figure()
        plt.plot(self.time[:self.acf.shape[1]], self.acf.mean(axis=0))
        plt.xlabel('Time (ns)', fontsize=14)
        plt.ylabel('Autocovariance', fontsize=14)
        plt.gcf().get_axes()[0].tick_params(labelsize=14)
        plt.tight_layout()

        if show:
            plt.show()

    def step_autocovariance(self):
        """ Calculate autocovariance of fractional gaussian noise in the trajectories (i.e. the step lengths)
        """

        self.acov = timeseries.autocov((self.com[1:, :, self.axis] - self.com[:-1, :, self.axis]).T[0, ...])

    def plot_autocovariance(self, show=True):
        """ Plot autocovariance function

        :param show: show plot

        :type show: bool
        """

        plt.figure()
        plt.plot(self.time[:-1], self.acov, color='blue', linewidth=3)
        plt.xlabel('Time Lag', fontsize=14)
        plt.ylabel('Autocovariance', fontsize=14)
        plt.gcf().get_axes()[0].tick_params(labelsize=14)
        plt.tight_layout()

        if show:
            plt.show()

    def update_database(self, wt_water, file="../timeseries/msd.db", tablename="msd", ensemble=False, frac=0.5):
        """ Update SQL database with information from this run

        :param wt_water: weight percent of water in system
        :param file: relative path (relative to directory where this script is stored) to database to be updated
        :param tablename: name of table being modified in database
        :param ensemble: True if ensemble MSD was calculated
        :param frac: Maximum time lag expressed as fraction of total simulation length

        :type wt_water: float
        :type file: str
        :type tablename: str
        :type ensemble: bool
        :type frac: float
        """

        connection = sql.connect("%s/%s" % (self.script_location, file))
        crsr = connection.cursor()

        check_existence = "SELECT COUNT(1) FROM %s WHERE name = '%s' and wt_water = %.1f and F = %.2f" % (tablename,
                           self.residue, wt_water, frac)

        output = crsr.execute(check_existence).fetchall()

        msd = self.MSD_average[self.endfit - 1]  # usually I would do :self.endfit so subtracting 1 here makes sense
        msd_lower = msd - self.limits[1, self.endfit - 1]
        msd_upper = msd + self.limits[0, self.endfit - 1]

        if ensemble:
            data_labels = ['MD_MSD', 'MD_MSD_CI_lower', 'MD_MSD_CI_upper']
        else:
            data_labels = ['MD_TAMSD', 'MD_TAMSD_CI_lower', 'MD_TAMSD_CI_upper']

        if output[0][0] > 0:

            update_entry = "UPDATE %s SET %s = %.3f, %s = %.3f, %s = %.3f, sim_length = %.2f where name = '%s' and " \
                           "wt_water = %.1f and F = %.2f" % \
                           (tablename, data_labels[0], msd, data_labels[1], msd_lower, data_labels[2], msd_upper,
                            self.time[-1], self.residue, wt_water, frac)
            print(update_entry)

            crsr.execute(update_entry)

        else:

            fill_new_entry = "INSERT INTO %s (name, %s, %s, %s, wt_water, sim_length, F) VALUES ('%s', %.3f, %.3f," \
                             "%.3f, %.1f, %.2f, %.2f)" % (tablename, data_labels[0], data_labels[1], data_labels[2],
                                                          self.residue, msd, msd_lower, msd_upper, wt_water,
                                                          self.time[-1], frac)

            crsr.execute(fill_new_entry)

        connection.commit()
        connection.close()


if __name__ == "__main__":

    args = initialize().parse_args()

    if args.compare:

        show = False

        D_ensemble = Diffusivity(args.trajectory, args.gro, args.axis, residue=args.residue, atoms=args.atoms)

        if args.pores or args.tails:  # do this if solutes are restricted to tails or pores
            D_ensemble.restrict_to_pore(args.pore_radius, tails=args.tails)

        D_ensemble.calculate(ensemble=True)
        # D_ensemble.fit_linear()  # make sure diffusivity is being measured from linear region of the MSD curve
        D_ensemble.bootstrap(args.nboot)
        D_ensemble.plot(fracshow=args.fracshow)
        print('D = %1.2e +/- %1.2e cm^2/s' % (D_ensemble.Davg, np.abs(D_ensemble.Davg -
                                                                     D_ensemble.confidence_interval[0])))
    else:
        show = True

    print(args.begin, args.end)
    D = Diffusivity(args.trajectory, args.gro, args.axis, begin=args.begin, end=args.end,
                    residue=args.residue, atoms=args.atoms)

    if args.pores or args.tails:  # do this if solutes are restricted to tails or pores
        D.restrict_to_pore(args.pore_radius, tails=args.tails)

    D.calculate(ensemble=args.ensemble)

    if args.power_law and not args.nofit:
        D.bootstrap_power_law(args.nboot)
        D.plot_power_law()
    else:
        if not args.compare and not args.nofit:
            D.fit_linear()  # make sure diffusivity is being measured from the linear region of the MSD curve

    if args.autocorrelation:
        D.step_autocorrelation()
        D.plot_autocorrelation()

    if args.autocovariance:
        D.step_autocovariance()
        D.plot_autocovariance()

    D.bootstrap(args.nboot)

    if args.savename:
        file_rw.save_object(D, '%s.pl' % args.savename)

    if args.ensemble:
        args.fracshow = 1  # same amount of statistics at each frame

    show = False
    D.plot(fracshow=args.fracshow, show=show, save=True)

    if args.update:
        D.update_database(args.wt_water, ensemble=args.ensemble, frac=args.fracshow)

    if not args.power_law and not args.nofit:
        print('D = %1.2e +/- %1.2e cm^2/s' % (D.Davg, np.abs(D.Davg - D.confidence_interval[0])))

    if args.compare:

        plt.figure()
        end_frame = int(args.fracshow * D.time.size)

        plt.fill_between(D.time[:end_frame], D.MSD_average[:end_frame] + D.limits[0, :end_frame],
                         D.MSD_average[:end_frame] - D.limits[1, :end_frame], alpha=0.7, label='Time-averaged MSD')

        end_frame = int(args.fracshow * D.time.size)
        plt.fill_between(D_ensemble.time[:end_frame], D_ensemble.MSD_average[:end_frame] +
                         D_ensemble.limits[0, :end_frame], D_ensemble.MSD_average[:end_frame] -
                         D_ensemble.limits[1, :end_frame], alpha=0.7, label='Ensemble-averaged MSD')

        plt.ylabel('MSD ($nm^2$)', fontsize=14)
        plt.xlabel('time (ns)', fontsize=14)
        plt.legend(fontsize=14, loc=2)
        plt.gcf().get_axes()[0].tick_params(labelsize=14)
        plt.tight_layout()
        #plt.savefig('/home/bcoscia/PycharmProjects/LLC_Membranes/Ben_Manuscripts/transport/figures/ethanol_msd_comparison.pdf')
        plt.show(block=True)
