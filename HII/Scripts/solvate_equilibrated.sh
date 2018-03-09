#!/usr/bin/env bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )" # the directory where this script is located.

# To get this going, all you need is an equilibrated configuration (produce using equil.sh)

GRO="wiggle.gro"  # initial dry equilibrated configuration
n=1038  # number of water molecules to insert
t=1000 # picoseconds of simulation time between insertions
p=0.6  # probability of finding a water molecule in the pore vs. tail region. e.g. 3:2 ratio corresponds to p = 0.6
emsteps=50  # number of steps to take during energy minimization directly after adding water
MPI=0  # set to 1 if you are running MPI
proc=4 # number of processes
python='python3'

while getopts "g:t:n:e:m:p:r:" opt; do
    case $opt in
        g) GRO=$OPTARG;;
        t) t=$OPTARG;;
        n) n=$OPTARG;;
        e) emsteps=$OPTARG;;
        m) MPI=$OPTARG;;
        p) p=$OPTARG;;
        r) proc=$OPTARG;;
    esac
done

if [[ ${MPI} -eq 1 ]]; then
    GMX="mpirun -np ${proc} gmx_mpi"
else
    GMX="gmx"
fi

# generate topology to be modified
input.py -c ${GRO}

cp ${GRO} npt.gro
mv topol.top topol_dry.top

for i in $(seq 1 ${n}); do

    echo "Adding water molecule # ${i}"
    # place water molecule in structure
    ${python} ${DIR}/solvate_equilibrated.py -g npt.gro -t topol_dry.top -p ${p} --emsteps ${emsteps}

    # create run files. Use berendsen barostat since the system was minimized and new molecules have been added
    input.py -S -l ${t} -f 5 --barostat berendsen --genvel 'yes' -c em.gro

    # run simulation. top_intermediate.top was generated by solvate_equilibrated.py
    ${GMX} grompp -f npt.mdp -p top_intermediate -c em.gro -o npt > /dev/null 2>&1
    ${GMX} mdrun -v -deffnm npt

    rm \#*

done