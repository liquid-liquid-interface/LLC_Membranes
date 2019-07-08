#!/usr/bin/env python

import argparse
from LLC_Membranes.setup.bcc_build import BicontinuousCubicBuild
from LLC_Membranes.setup.gentop import SystemTopology
from LLC_Membranes.setup.genmdp import SimulationMdp
from LLC_Membranes.llclib import topology, transform, gromacs
from LLC_Membranes.setup import xlink
import mdtraj as md
import os
import numpy as np
import subprocess


def initialize():

    parser = argparse.ArgumentParser(description='Equilibrate bicontinuous cubic phase unit cell')

    # Build parameters
    parser.add_argument('-p', '--space_group', default='gyroid', type=str, help='Liquid crystal phase to build (HII, QI '
                                                                              'etc.)')
    parser.add_argument('-b', '--build_monomer', default='Dibrpyr14', type=str, help='Name of single monomer'
                        'structure file (.gro format) used to build full system')
    parser.add_argument('-o', '--out', default='initial.gro', help='Name of output .gro file for full system')
    parser.add_argument('-seed', '--random_seed', default=False, type=int, help='Random seed for column shift. Set this'
                                                                                'to reproduce results')
    parser.add_argument('-box', '--box_length', type=float, default=10., help='Length of box vectors [x y z]')
    parser.add_argument('-g', '--grid', default=50, type=int, help='Number of sections to break grid into when '
                                                                'approximating the chosen implicit function')
    parser.add_argument('-dens', '--density', default=1.1, type=float, help='Density of system (g/cm3)')
    parser.add_argument('-c', '--curvature', default=1, type=float,
                        help='mean curvature of the system. A value of 0, < 0 and > 0 correspond to zero, negative and'
                             'positive mean curvature respectively.')
    parser.add_argument('-wt', '--weight_percent', default=77.1, type=float, help='Weight %% of monomer in membrane')
    parser.add_argument('-sol', '--solvent', default='GLY', type=str, help='Name of solvent residue mixed with monomer')
    parser.add_argument('-shift', '--shift', default=1, type=float, help='Shift position of head group shift units in'
                        'the direction opposite of the normal vector at that point')
    parser.add_argument('-res', '--residue', default='MOL', help='Name of residue corresponding to build monomer')
    parser.add_argument('-resd', '--dummy_residue', default='MOLd', help='Name of residue to be cross-linked with '
                                                                         'dummy atoms included in the topology')

    # simulation parameters
    parser.add_argument('-T', '--temperature', default=343, type=float, help='Temperature at which to simulate')
    parser.add_argument('-f', '--scale_factor', default=2, type=float, help='Factor by which to isotropically scale'
                                                                            'box dimensions during initial setup')

    # run options
    parser.add_argument('-mpi', '--mpi', action="store_true", help='Parallelize computation via MPI')
    parser.add_argument('-np', '--nprocesses', default=4, type=int, help='Number of MPI processes. Only if `mpi` is '
                                                                         'true')
    parser.add_argument('-dd', '--domain_decomposition', default=[2, 2, 1], help='xyz dimensions of domain '
                        'decomposition grid. This may need to be adjusted if there are issues with mdrun. The product'
                        'of these values should equal the number of processes (np)')

    return parser


class EnsembleError(Exception):
    """ Raised if invalid thermodynamic ensemble is passed """

    def __init__(self, message):

        super().__init__(message)


class EquilibrateBCC(topology.LC):

    def __init__(self, build_monomer, space_group, box_length, weight_percent, density, shift=0, curvature=-1,
                 mpi=False, nprocesses=4):
        """
        :param build_monomer: name of monomer with which to build phase
        :param space_group: name of space group into which monomers are arranged (i.e. gyroid, schwarzD etc.)
        :param box_length: length of each edge of the unit cell (nm). The box is cubic, so they must all be the same (or
        just one length specified)
        :param weight_percent: percent by weight of monomer in membrane
        :param density: experimentally derived density of membrane
        :param shift: translate monomer along vector perpendicular to space group surface by this amount (nm). This
        parameter effectively controls the pore size
        :param curvature: determines whether the phase is normal or inverted. {-1 : QI phase, 1: QII phase}'
        :param mpi: parallelize GROMACS commands using MPI
        :param nprocesses: number of MPI processes if `mpi` is true

        :type monomer: str
        :type space_group: str
        :type dimensions: float, list of floats
        :type weight_percent: int, float
        :type density: float
        :type shift: float
        :type curvature: int
        :type mpi: bool
        :type nprocesses: int
        """

        super().__init__(build_monomer)

        self.build_monomer = build_monomer
        self.space_group = space_group
        self.period = box_length
        self.weight_percent = weight_percent
        self.density = density
        self.shift = shift
        self.curvature = curvature

        # names of files (hard coded for now since it's not really important
        self.em_mdp = 'em.mdp'
        self.nvt_mdp = 'nvt.mdp'
        self.npt_mdp = 'npt.mdp'
        self.top_name = 'topol.top'

        self.topology = None
        self.system = None
        self.gro_name = None  # name of .gro file
        self.mdp = None
        self.solvent = None
        self.nsolvent = 0

        # parallelization
        self.mpi = mpi
        self.nprocesses = nprocesses

    def build_initial_config(self, grid_points=50, r=0.5, name='initial.gro'):
        """ Build an initial configuration with parameters specified in the __init__ function

        :param grid_points: number of grid points to use when approximating implicit surface
        :param r: distance between monomer placement points (no monomers will be placed within a sphere of radius, r
        from a given monomer.
        :param name: name of output configuration

        :type grid_points: int
        :type r: float
        :type name: str
        """

        self.system = BicontinuousCubicBuild(self.build_monomer, self.space_group, self.period, self.weight_percent,
                                             self.density)

        self.system.gen_grid(grid_points, self.curvature)

        self.system.determine_monomer_placement(r=r)

        self.system.place_monomers(shift=self.shift)

        self.system.reorder()

        self.system.write_final_configuration(name=name)

        self.gro_name = name

    def generate_topology(self, name='topol.top'):
        """ Generate a topology for the current unit cell
        """

        self.topology = SystemTopology(self.gro_name)
        self.topology.write_top(name=name)

    def generate_mdps(self, T=300, em_steps=-1, time_step=0.002, length=1000, p_coupling='isotropic',
                      barostat='berendsen', genvel='yes', restraints=False, xlink=False, tau_p=20, tau_t=0.1,
                      nstxout=5000, nstvout=5000, nstfout=5000, nstenergy=5000, frames=None):

        """ Create an object that can be used to generate GROMACS .mdp files

        :param T: (float) simulation temperature
        :param em_steps: (int) number of steps to take during energy minimization
        :param time_step: (float) simulation time step (fs)
        :param length: (int) simulation length, picoseconds
        :param p_coupling: (str) type of pressure coupling (None, semiisotropic, isotropic etc.)
        :param barostat: (str) barostat to use for pressure control
        :param genvel: (bool) True if velocities should be generated for initial configuration. \
        if you want to use velocities already present in coordinate file
        :param restraints: (bool) whether or not the system has been restrained (meaning a special topology file has \
        been created
        :param xlink: (bool) whether the system is being run through the crosslinking algorithm
        :param tau_p: (int) time constant for pressure coupling
        :param tau_t: (int) time constant for temperature coupling
        :param nstxout: frequency of outputting coordinates to trajectory file
        :param nstvout: frequency of outputting velocity to trajectory file
        :param nstfout: frequency of outputting forces to trajectory file
        :param nstenergy: frequency of outputting energy to energy file
        :param frames: number of frames to output. If not None, nstxout, nstvout, nstfou and nstenergy will be \
        adjusted accordingly
        """

        self.mdp = SimulationMdp(self.gro_name, bcc=True, T=T, em_steps=em_steps, time_step=time_step, length=length,
                                 p_coupling=p_coupling, barostat=barostat, genvel=genvel, restraints=restraints,
                                 xlink=xlink, tau_p=tau_p, tau_t=tau_t, nstxout=nstxout, nstvout=nstvout,
                                 nstfout=nstfout, nstenergy=nstenergy, frames=frames)

    def scale_unit_cell(self, factor, name='scaled.gro'):
        """ Isotropically expand unit cell by some factor

        :param factor: factor by which to scale each dimension
        :param name: name of scaled unit cell

        :type factor: float
        :type name: str
        """

        t = md.load(self.gro_name)  # load up unit cell
        box = t.unitcell_lengths[0]

        indices = []
        atoms_per_residue = []

        for res in self.residues:

            atoms_per_residue.append(self.LC_residues.count(res))
            indices.append([a.index for a in t.topology.atoms if a.residue.name == res])  # indices that are part of residue

        for m in range(self.system.nmon):

            ndx = []
            for r in range(len(atoms_per_residue)):
                ndx += indices[r][m*atoms_per_residue[r]:(m + 1)*atoms_per_residue[r]]

            before = t.xyz[0, ndx[0], :]  # a reference atom
            after = before * factor  # isotropically scale it's position

            # coordinates of all atoms in group to be moved
            initial = t.xyz[0, ndx, :]

            # translate residue to `after` location
            self.system.final_positions[ndx, :] = transform.translate(initial, before, after)

        self.system.write_final_configuration(name=name, box=[factor*x for x in box])

        self.gro_name = name

    def shrink_unit_cell(self, start, stop, step):
        """ shrink the Q1 phase unit cell by isotropically scaling monomer positions and box lengths. Runs an energy
        minimization between each shrinkage and reduces the rate of shrinkage if an energy minimization fails.

        :param system: EquilibrateBCC object
        :param start: scale of unit cell relative to desired scale
        :param stop: final scale of unit cell relative to desired scale
        :param step: how much to decrease scale each iteration

        :type system: EquilibrateBCC object
        :type start: float
        :type stop: float
        :type step: float
        """

        for i in np.linspace(start - step, stop, int(round((start - stop) / step))):

            f = i / (i + step)
            self.scale_unit_cell(f)
            print('Attempting energy minimization with box lengths %.4f times desired size' % i, end='', flush=True)
            nrg = gromacs.simulate(self.em_mdp, self.top_name, self.gro_name, self.em_mdp.split('.')[0],
                                   em_energy=True, verbose=False, mpi=self.mpi, nprocesses=self.nprocesses)

            if nrg >= 0:
                print('...Failed. Will shrink slower on next iteration.')
                self.gro_name = 'scaled_%.4f.gro' % (i + step)
                self.shrink_unit_cell(i + step, i, step / 2)  # reduce shrink rate
            else:
                print('...Success!')

            cp = 'cp em.gro scaled_%.4f.gro' % i
            p = subprocess.Popen(cp.split())
            p.wait()

    def insert_molecules(self, scale, total, ensemble):
        """ Insert solvent molecules. Used by add_solvent

        :param scale: factor by which to scale van der waals radii when inserting solvent molecules with gmx solvate
        :param total: the total number of solvent molecules added to the system before calling this function
        :param ensemble: thermodynamic ensemble in which to simulate system

        :type scale: float
        :type total: int
        :type ensemble: str

        :return delta: number of solutes added

        :rtype delta: int
        """

        if ensemble.upper() == 'NVT':
            mdp = self.nvt_mdp
        elif ensemble.upper() == 'NPT':
            mdp = self.npt_mdp
        else:
            raise EnsembleError("%s is not a valid (or not implemented) thermodynamic ensemble" % ensemble)

        delta = gromacs.insert_molecules(self.gro_name, self.solvent.name, self.nsolvent, 'solvated.gro',
                                         scale=scale)
        total += delta
        print('Inserted %d %s molecules for a total of %d' % (delta, self.solvent.name, total))

        if delta != 0:  # prevents unnecessary energy minimizations

            self.topology.add_residue(self.solvent, n=delta, write=True, topname=self.top_name)
            print('Energy minimizing...', end='', flush=True)
            gromacs.simulate(self.em_mdp, self.top_name, 'solvated.gro', 'em_%d' % total, mpi=self.mpi,
                             nprocesses=self.nprocesses)  # energy minimize
            print('Done!')

            # short simulation
            print('Running %s simulation...' % ensemble.upper(), end='', flush=True)
            gromacs.simulate(mdp, self.top_name, 'em_%d' % total, 'solvated_%s_%d' % (ensemble.lower(), total),
                             mpi=self.mpi, nprocesses=self.nprocesses)
            print('Done!')

            self.gro_name = 'solvated_%s_%d.gro' % (ensemble.lower(), total)
            self.nsolvent -= delta

        return delta

    def add_solvent(self, solvent, tol=10, scale=0.4, nvt_length=1000, npt_length=1000, out='solvated_final.gro'):
        """ Name of solvent residue to add to structure. This is an iterative process of solvent insertion, energy
        minimzation, and an nvt simulation

        :param solvent: residue name to add
        :param tol: Once an iteration can fit no more than tol solvent atoms into structure (with gmx insert-molecules),
        the iteration stops
        :param scale: factor by which to scale van der waals radii when inserting solvent molecules with gmx solvate
        :param nvt_length: length of long NVT simulation (ps)
        :param npt_length: length of long NPT simulation (ps)

        :type solvent: str
        :type tol: int
        :type scale: float
        :type nvt_length: int
        :type npt_length: int
        """

        # figure out number of solvent molecules to add
        self.solvent = topology.Residue(solvent)  # get solvent properties
        mass_solvent = self.system.nmon * self.system.MW * ((100 - self.weight_percent) / self.weight_percent)
        self.nsolvent = int(mass_solvent / self.solvent.MW)  # total number of solutes to add

        # create nvt mdp file
        self.mdp.write_nvt_mdp(length=50)

        print('Attempting to insert %d solvent molecules' % self.nsolvent)

        delta = self.nsolvent  # convergence parameter
        total = 0  # for naming
        while self.nsolvent > 0 and delta > tol:
            delta = self.insert_molecules(scale, total, "NVT")
            total += delta

        # run a longer NVT simulation
        print("Running %d ps NVT equilibration" % nvt_length)
        self.mdp.write_nvt_mdp(length=nvt_length)
        gromacs.simulate(self.nvt_mdp, self.top_name, self.gro_name, 'nvt_equil', mpi=self.mpi,
                         nprocesses=self.nprocesses)

        # run a short NPT simulation
        self.mdp.write_npt_mdp(length=50)
        gromacs.simulate(self.npt_mdp, self.top_name, 'nvt_equil.gro', 'npt_%d' % total, mpi=self.mpi,
                         nprocesses=self.nprocesses)
        self.gro_name = 'npt_%d.gro' % total

        # run series of NPT simulations to try and stuff more solutes in
        while self.nsolvent > 0 and delta > tol:
            delta = self.insert_molecules(scale, total, "NPT")
            total += delta

        # run a longer NPT simulation
        print("Running %d ps NPT equilibration" % npt_length)
        self.mdp.write_npt_mdp(length=npt_length)
        gromacs.simulate(self.npt_mdp, self.top_name, self.gro_name, 'npt_equil', mpi=self.mpi,
                         nprocesses=self.nprocesses)
        self.gro_name = 'npt_equil.gro'

        # rename things to desired final output name
        cp = 'cp %s %s' % (self.gro_name, out)
        p = subprocess.Popen(cp.split())
        p.wait()

        self.gro_name = out

        print('Successfully inserted %s %s molecules' % (total, self.solvent.name))


if __name__ == "__main__":

    args = initialize().parse_args()

    os.environ["GMX_MAXBACKUP"] = "-1"  # stop GROMACS from making backups

    # equil = EquilibrateBCC(args.build_monomer, args.space_group, args.box_length, args.weight_percent, args.density,
    #                        shift=args.shift, curvature=args.curvature, mpi=args.mpi, nprocesses=args.nprocesses)
    #
    # equil.build_initial_config(grid_points=args.grid, r=0.5)
    # equil.scale_unit_cell(args.scale_factor)
    #
    # equil.generate_topology(name='topol.top')  # creates an output file
    # equil.generate_mdps(length=50, frames=2, T=args.temperature)  # creates an object
    # equil.mdp.write_em_mdp(out='em')
    #
    # nrg = gromacs.simulate('em.mdp', 'topol.top', equil.gro_name, 'em', em_energy=True, verbose=True, mpi=args.mpi,
    #                        nprocesses=args.nprocesses)  # mdp, top, gro, out
    #
    # while nrg >= 0:
    #
    #     equil.build_initial_config(grid_points=args.grid, r=0.5)
    #     equil.scale_unit_cell(args.scale_factor)
    #     nrg = gromacs.simulate('em.mdp', 'topol.top', equil.gro_name, 'em', em_energy=True, verbose=True, mpi=args.mpi,
    #                            nprocesses=args.nprocesses)
    #
    # cp = 'cp em.gro scaled_%.4f.gro' % args.scale_factor
    # p = subprocess.Popen(cp.split())
    # p.wait()
    #
    # equil.gro_name = 'scaled_%.4f.gro' % args.scale_factor
    #
    # # slowly compress system to correct density
    # equil.shrink_unit_cell(args.scale_factor, 1.0, 0.1)  # EquilibrateBCC object, start, stop, step
    #
    # equil.add_solvent(args.solvent)
    #
    # # cross-linking
    # params = vars(xlink.initialize().parse_args())  # get default arguments passed to xlink
    # # modify certain params for this system.
    # params['initial'] = equil.gro_name
    # params['build_mon'] = args.build_monomer
    # params['temperature'] = args.temperature
    # params['residue'] = args.residue
    # params['dummy_residue'] = args.dummy_residue
    # params['parallelize'] = args.mpi
    # params['nproc'] = args.nprocesses
    # params['domain_decomposition'] = args.domain_decomposition
    #
    # xlink.crosslink(params)  # run cross-linking algorithm

    # Can use this to continue from an expanded then shrunk initial configuration
    equil = EquilibrateBCC(args.build_monomer, args.space_group, args.box_length, args.weight_percent, args.density,
                           shift=args.shift, curvature=args.curvature, mpi=args.mpi, nprocesses=args.nprocesses)

    equil.build_initial_config(grid_points=args.grid, r=0.5)

    equil.generate_topology(name='topol.top')  # creates an output file
    equil.generate_mdps(length=50, frames=2, T=args.temperature)  # creates an object
    equil.mdp.write_em_mdp(out='em')

    equil.gro_name = "scaled_1.0000.gro"

    equil.add_solvent(args.solvent, scale=0.45)

    params = vars(xlink.initialize().parse_args())  # get default arguments passed to xlink
    # modify certain params for this system.
    params['initial'] = equil.gro_name
    params['build_mon'] = args.build_monomer
    params['temperature'] = args.temperature
    params['residue'] = args.residue
    params['dummy_residue'] = args.dummy_residue
    params['parallelize'] = args.mpi
    params['nproc'] = args.nprocesses
    params['domain_decomposition'] = args.domain_decomposition

    xlink.crosslink(params)  # run cross-linking algorithm

