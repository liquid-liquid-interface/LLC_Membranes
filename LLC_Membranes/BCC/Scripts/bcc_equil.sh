#!/usr/bin/env bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )" # the directory where this script is located.
DIR2="${DIR}/../../setup"  # some scripts aren't located in the same directory as $DIR

build_mon="Dibrpyr14"  # monomer to build system with
phase="gyroid"  # bcc phase to build
dimension=10  # length of cubic unitcell box vector
density=1.1  # density g/cm^3
SHIFT=0  # shift head group position along normal (lowercase 'shift' is bash built-in command)
ion="BR"
wt_percent=77.1  # weight percent of monomer
solvent='glycerol'  # name of solvent with bcc monomer
top='topol.top'  # name of topology
temp=343
restraint_atoms="C23 C17 C28"
sol_res='GLY' # solvent residue name
cluster=0  # 0 if run without mpi, 1 if run with mpi (regular MPI or with GPU)
NP=4  # number of MPI processes
python='python3'
scale_factor=2  # how to scale each dimension of unit cell. A scale factor of 2 will double each dimension with corresponding isotropic expansion

while getopts "b:p:d:r:n:I:w:S:T:R:c:P:f:" opt; do
    case $opt in
    b) build_mon=$OPTARG;;
    p) phase=$OPTARG;;
    d) dimension=$OPTARG;;
    r) density=$OPTARG;;
    n) SHIFT=$OPTARG;;
    I) ion=$OPTARG;;
    w) wt_percent=$OPTARG;;
    S) solvent=$OPTARG;;
    T) temp=$OPTARG;;
    R) restraint_atoms=$OPTARG;;
    c) cluster=$OPTARG;;
    P) NP=$OPTARG;;
    f) scale_factor=$OPTARG;;
    esac
done

if [[ ${cluster} -eq 1 ]]; then
    GMX="mpirun -np ${NP} gmx_mpi"
else
    GMX="gmx"
fi

#${python} ${DIR}/bcc_build.py -b ${build_mon}.gro -p ${phase} -d ${dimension} -dens ${density} -shift ${SHIFT} -o initial.gro -wt ${wt_percent} -sol ${solvent}
#${python} ${DIR2}/reorder_gro.py -i initial.gro -o bcc.gro -I ${ion}
#echo "Initial unit cell built"
#${python} ${DIR2}/input.py --bcc -b ${build_mon} -s 5000 # only care about em.mdp and topol.top at this step
#
## really spread the monomers apart
#${python} ${DIR}/scale.py -g bcc.gro -o bcc.gro -f ${scale_factor}  # triple each dimension of the unit cell and isotropically scale all head group positions
#
#gmx grompp -f em.mdp -p topol.top -c bcc.gro -o em
#${GMX} mdrun -v -deffnm em
#
## Try again if the simulation doesn't energy minimize properly
#n=$(awk '/Potential Energy/ {print $4}' em.log)  # gets the final value of potential energy from em.log (the 4th field on the line containing the string "Potential Energy"
#if [[ ${n:0:2} == *"."* ]]; then # check the first two characters of the potential energy value. If there is a decimal, then it is positive
#    rm \#*
#    new_scale=$(echo "${scale_factor} + 0.1" | bc -l)
#    exec bcc_equil.sh -p ${phase} -f ${new_scale} -n ${SHIFT} -d ${dimension} -c ${cluster} -P ${NP} -b ${build_mon} -r ${density} -I ${ion} -w ${wt_percent} -S ${solvent} -T ${temp} -R ${restraint_atoms}  # restart this script
#fi
#
#echo 0 | gmx trjconv -f em.trr -s em.tpr -pbc nojump -o bcc.gro
#cp bcc.gro bcc_${scale_factor}.gro
#
#for i in $(seq $(echo "${scale_factor} - 0.1" | bc -l) -0.1 1.0); do
#
#    factor=$(echo "${i} / (${i} + 0.1)" | bc -l)
#    ${python} ${DIR}/scale.py -g bcc.gro -o bcc.gro -f ${factor}
#    gmx grompp -f em.mdp -p topol.top -c bcc.gro -o em
#    ${GMX} mdrun -v -deffnm em
#
#    # in case of more energy minimization problems
#    n=$(awk '/Potential Energy/ {print $4}' em.log)
#    if [[ ${n:0:2} == *"."* ]]; then
#        step=$(echo "${i} + 0.1" | bc -l)
#        cp bcc_${step}.gro bcc.gro
#        for j in $(seq $(echo "${i} + 0.08" |bc -l) -0.02 ${i}); do
#            factor=$(echo "${j} / (${j} + 0.02)" | bc -l)
#            ${python} ${DIR}/scale.py -g bcc.gro -o bcc.gro -f ${factor}
#            gmx grompp -f em.mdp -p topol.top -c bcc.gro -o em
#            ${GMX} mdrun -v -deffnm em
#            echo 0 | gmx trjconv -f em.trr -s em.tpr -pbc nojump -o bcc.gro
#            cp bcc.gro bcc_${j}.gro
#        done
#    fi
#    echo 0 | gmx trjconv -f em.trr -s em.tpr -pbc nojump -o bcc.gro
#    cp bcc.gro bcc_${i}.gro
#
#done

LOC="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

nsol=$(${python} ${DIR}/nsolvent.py -b ${build_mon}.gro -d ${dimension} -dens ${density} -wt ${wt_percent} -sol ${solvent})
ntot=0

echo "Trying to insert ${nsol} ${solvent} molecules"
gmx insert-molecules -f bcc.gro -ci ${LOC}/../top/structures/${solvent}.gro -nmol ${nsol} -o initial.gro &> placement.txt
nadded=$(awk '/Added/ {print $2}' placement.txt)  # the actual amount of molecules gromacs was able to place
ntot=$((nadded + ntot))

${python} ${DIR2}/input.py --bcc -b ${build_mon} -l 50 --temp ${temp} -f 50 -e nvt --restraints MOL
${python} ${DIR2}/restrain.py -a ${restraint_atoms} -f 100 100 100 -m MOL -g bcc.gro -A xyz

gmx grompp -f em.mdp -p topol.top -c initial.gro -o em -r initial.gro
${GMX} mdrun -v -deffnm em
#echo 0 | gmx trjconv -f em.trr -s em.tpr -pbc nojump -o em.gro -b 50

gmx grompp -f nvt.mdp -p topol.top -c em.gro -o nvt -r em.gro
${GMX} mdrun -v -deffnm nvt
echo 0 | gmx trjconv -f nvt.trr -s nvt.tpr -pbc nojump -o nvt.gro -b 50

while [[ ${ntot} -lt ${nsol} ]]; do

    gly2add=$((nsol - ntot))  # amount of glycerol to add in order to reach nsol
    gmx insert-molecules -f nvt.gro -ci ${LOC}/../top/structures/${solvent}.gro -nmol ${gly2add} -o initial.gro -scale 0.4 &> placement.txt
    nadded=$(awk '/Added/ {print $2}' placement.txt)  # the actual amount of molecules gromacs was able to place
    if [[ ${nadded} -lt 10 ]]; then  # if an insignificant amount (10) of glycerol is added, stop trying to add more
        break
    fi
    echo "${sol_res}         ${nadded}" >> topol.top
    ntot=$((nadded + ntot))
    gmx grompp -f em.mdp -p topol.top -c initial.gro -o em -r initial.gro
    ${GMX} mdrun -v -deffnm em
    echo 0 | gmx trjconv -f em.trr -s em.tpr -pbc nojump -o em.gro
    gmx grompp -f nvt.mdp -p topol.top -c em.gro -o nvt -r em.gro
    ${GMX} mdrun -v -deffnm nvt
    echo 0 | gmx trjconv -f nvt.trr -s nvt.tpr -pbc nojump -o nvt.gro -b 50
    cp nvt.gro nvt_${ntot}.gro

done

# a 'long' nvt equilibration
${python} ${DIR2}/input.py --bcc -b ${build_mon} -l 5000 --temp ${temp} -f 50 --genvel yes -e nvt -S --solvent glycerol --restraints
echo "${sol_res}              ${ntot}" >> topol.top

# restrain the head groups and tails during nvt equilibration
${python} ${DIR2}/restrain.py -a ${restraint_atoms} -f 100 100 100 -m ${build_mon} -g em.gro -A xyz

gmx grompp -f nvt.mdp -p topol.top -c nvt.gro -o nvt
${GMX} mdrun -v -deffnm nvt
echo 0 | gmx trjconv -f nvt.trr -s nvt.tpr -pbc nojump -o nvt.gro -b 5000

# switching to npt ensemble
${python} ${DIR2}/input.py --bcc -b ${build_mon} -l 50 --temp ${temp} -f 50 --genvel no -e npt -S --solvent glycerol
echo "${sol_res}              ${ntot}" >> topol.top

gmx grompp -f npt.mdp -p topol.top -c nvt.gro -o npt
${GMX} mdrun -v -deffnm npt

while [[ ${ntot} -lt ${nsol} ]]; do

    gly2add=$((nsol - ntot))  # amount of glycerol to add in order to reach nsol
    gmx insert-molecules -f npt.gro -ci ${LOC}/../top/structures/${solvent}.gro -nmol ${gly2add} -o initial.gro -scale 0.4 &> placement.txt
    nadded=$(awk '/Added/ {print $2}' placement.txt)  # the actual amount of molecules gromacs was able to place
    if [[ ${nadded} -lt 10 ]]; then  # if an insignificant amount (10) of glycerol is added, stop trying to add more
        break
    fi
    echo "${sol_res}         ${nadded}" >> topol.top
    ntot=$((nadded + ntot))
    gmx grompp -f em.mdp -p topol.top -c initial.gro -o em
    ${GMX} mdrun -v -deffnm em
    echo 0 | gmx trjconv -f em.trr -s em.tpr -pbc nojump -o em.gro
    gmx grompp -f npt.mdp -p topol.top -c em.gro -o nvt
    ${GMX} mdrun -v -deffnm npt
    echo 0 | gmx trjconv -f npt.trr -s npt.tpr -pbc nojump -o npt.gro -b 50
    cp npt.gro npt_${ntot}.gro

done

# long npt simulation
${python} ${DIR2}/input.py --bcc -b ${build_mon} -l 5000 --temp ${temp} -f 50 --genvel no -e npt -S --solvent glycerol
echo "${sol_res}              ${ntot}" >> topol.top

gmx grompp -f npt.mdp -p topol.top -c npt.gro -o npt
${GMX} mdrun -v -deffnm npt
