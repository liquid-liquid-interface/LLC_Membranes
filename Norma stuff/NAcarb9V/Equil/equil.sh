#!/bin/bash

set -e  # exit immediately after error

BUILD_MON="NAcarb9V"
x=8.36
y=8.36
z=8
ring_restraints="C1 C2 C3 C16 C29 C42"

while getopts "b:x:y:z:r:" opt; do
	case $opt in
	b) BUILD_MON=$OPTARG;;
	x) x=$OPTARG;;
	y) y=$OPTARG;;
	z) z=$OPTARG;;
	r) ring_restraints=$OPTARG;;
	esac
done

input.py -b ${BUILD_MON} -l 50 -p semiisotropic --restraints
gmx editconf -f initial.gro -o box.gro -c -bt triclinic -box ${x} ${y} ${z} -angles 90 90 120
restrain.py -f 1000000 -A xyz -r on -D off -w off -g initial.gro --novsites -m ${BUILD_MON} -a ${ring_restraints}
gmx grompp -f em.mdp -p topol.top -c box.gro -o em
gmx mdrun -v -deffnm em
gmx grompp -f npt.mdp -p topol.top -c em.gro -o npt
gmx mdrun -v -deffnm npt
cp npt.gro 1000000.gro
cp npt.trr 1000000.trr

for f in 3162 56 8 3 2 1 0; do
	restrain.py -f ${f} -A xyz -r on -D off -w off --novsites -m ${BUILD_MON} -a ${ring_restraints}
	gmx grompp -f npt.mdp -p topol.top -c npt.gro -o npt
	gmx mdrun -v -deffnm npt
	cp npt.gro ${f}.gro
	cp npt.trr ${f}.trr
done