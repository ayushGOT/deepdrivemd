import logging
import random
import time
from pathlib import Path
from typing import Optional, Tuple, Union

import MDAnalysis
import numpy as np
import numpy.typing as npt

try:
    import openmm
    import openmm.app as app
    import openmm.unit as u
except ImportError:
    pass  # For testing purposes

from MDAnalysis.analysis import align, distances, rms

from deepdrivemd.applications.openmm_simulation import (
    ContinueSimulation,
    MDSimulationInput,
    MDSimulationOutput,
    MDSimulationSettings,
    SimulationFromPDB,
    SimulationFromRestart,
    SimulationStartType,
)
from deepdrivemd.utils import Application, parse_application_args

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]


def _configure_amber_implicit(
    pdb_file: PathLike,
    top_file: Optional[PathLike],
    dt_ps: float,
    temperature_kelvin: float,
    heat_bath_friction_coef: float,
    platform: "openmm.Platform",
    platform_properties: dict,
) -> Tuple["app.Simulation", Optional["app.PDBFile"]]:

    # Configure system
    if top_file is not None:
        pdb = None
        top = app.AmberPrmtopFile(str(top_file))
        system = top.createSystem(
            nonbondedMethod=app.CutoffNonPeriodic,
            nonbondedCutoff=1.0 * u.nanometer,
            constraints=app.HBonds,
            implicitSolvent=app.OBC1,
        )
    else:
        pdb = app.PDBFile(str(pdb_file))
        top = pdb.topology
        forcefield = app.ForceField("amber99sbildn.xml", "amber99_obc.xml")
        system = forcefield.createSystem(
            top,
            nonbondedMethod=app.CutoffNonPeriodic,
            nonbondedCutoff=1.0 * u.nanometer,
            constraints=app.HBonds,
        )

    # Configure integrator
    integrator = openmm.LangevinIntegrator(
        temperature_kelvin * u.kelvin,
        heat_bath_friction_coef / u.picosecond,
        dt_ps * u.picosecond,
    )
    integrator.setConstraintTolerance(0.00001)

    sim = app.Simulation(top, system, integrator, platform, platform_properties)

    # Returning the pdb file object for later use to reduce I/O.
    # If a topology file is passed, the pdb variable is None.
    return sim, pdb


def _configure_amber_explicit(
    top_file: PathLike,
    dt_ps: float,
    temperature_kelvin: float,
    heat_bath_friction_coef: float,
    platform: "openmm.Platform",
    platform_properties: dict,
    explicit_barostat: str,
) -> "app.Simulation":

    top = app.AmberPrmtopFile(str(top_file))
    system = top.createSystem(
        nonbondedMethod=app.PME,
        nonbondedCutoff=1.0 * u.nanometer,
        constraints=app.HBonds,
    )

    # Congfigure integrator
    integrator = openmm.LangevinIntegrator(
        temperature_kelvin * u.kelvin,
        heat_bath_friction_coef / u.picosecond,
        dt_ps * u.picosecond,
    )

    if explicit_barostat == "MonteCarloBarostat":
        system.addForce(
            openmm.MonteCarloBarostat(1 * u.bar, temperature_kelvin * u.kelvin)
        )
    elif explicit_barostat == "MonteCarloAnisotropicBarostat":
        system.addForce(
            openmm.MonteCarloAnisotropicBarostat(
                (1, 1, 1) * u.bar, temperature_kelvin * u.kelvin, False, False, True
            )
        )
    else:
        raise ValueError(f"Invalid explicit_barostat option: {explicit_barostat}")

    sim = app.Simulation(
        top.topology, system, integrator, platform, platform_properties
    )

    return sim


# TODO: Instead of the procedural abstraction, a strategy
# simulation object would be more modular and extensible.


def configure_simulation(
    pdb_file: PathLike,
    top_file: Optional[PathLike],
    solvent_type: str,
    gpu_index: int,
    dt_ps: float,
    temperature_kelvin: float,
    heat_bath_friction_coef: float,
    explicit_barostat: str = "MonteCarloBarostat",
    run_minimization: bool = True,
    set_positions: bool = True,
    set_velocities: bool = True,
) -> "app.Simulation":
    """Configure an OpenMM amber simulation.
    Parameters
    ----------
    pdb_file : PathLike
        The PDB file to initialize the positions (and topology if
        `top_file` is not present and the `solvent_type` is `implicit`).
    top_file : Optional[PathLike]
        The topology file to initialize the systems topology.
    solvent_type : str
        Solvent type can be either `implicit` or `explicit`, if `explicit`
        then `top_file` must be present.
    gpu_index : int
        The GPU index to use for the simulation.
    dt_ps : float
        The timestep to use for the simulation.
    temperature_kelvin : float
        The temperature to use for the simulation.
    heat_bath_friction_coef : float
        The heat bath friction coefficient to use for the simulation.
    explicit_barostat : str, optional
        The barostat used for an `explicit` solvent simulation can be either
        "MonteCarloBarostat" by deafult, or "MonteCarloAnisotropicBarostat".
    run_minimization : bool, optional
        Whether or not to run energy minimization, by default True.
    set_positions : bool, optional
        Whether or not to set positions (Loads the PDB file), by default True.
    set_velocities : bool, optional
        Whether or not to set velocities to temperature, by default True.
    Returns
    -------
    app.Simulation
        Configured OpenMM Simulation object.
    """
    # Configure hardware
    try:
        platform = openmm.Platform_getPlatformByName("CUDA")
        platform_properties = {"DeviceIndex": str(gpu_index), "CudaPrecision": "mixed"}
    except Exception:
        try:
            platform = openmm.Platform_getPlatformByName("OpenCL")
            platform_properties = {"DeviceIndex": str(gpu_index)}
        except Exception:
            platform = openmm.Platform_getPlatformByName("CPU")
            platform_properties = {}

    # Select implicit or explicit solvent configuration
    if solvent_type == "implicit":
        sim, pdb = _configure_amber_implicit(
            pdb_file,
            top_file,
            dt_ps,
            temperature_kelvin,
            heat_bath_friction_coef,
            platform,
            platform_properties,
        )
    else:
        assert solvent_type == "explicit"
        assert top_file is not None
        pdb = None
        sim = _configure_amber_explicit(
            top_file,
            dt_ps,
            temperature_kelvin,
            heat_bath_friction_coef,
            platform,
            platform_properties,
            explicit_barostat,
        )

    # Set the positions
    if set_positions:
        if pdb is None:
            pdb = app.PDBFile(str(pdb_file))
        sim.context.setPositions(pdb.getPositions())

    # Minimize energy and equilibrate
    if run_minimization:
        sim.minimizeEnergy()

    # Set velocities to temperature
    if set_velocities:
        sim.context.setVelocitiesToTemperature(
            temperature_kelvin * u.kelvin, random.randint(1, 10000)
        )

    return sim


# TODO: A more efficient (but complex) implementation could background the
# contact map and RMSD computation using openmm reporters using a process pool.
# This would overlap the simulations and analysis so they finish at roughly
# the same time.


class MDSimulationApplication(Application):
    config: MDSimulationSettings

    def __init__(self, config: MDSimulationSettings) -> None:
        super().__init__(config)
        # Store simulation state to enable continuing a given simulation
        self.sim: "app.Simulation" = None
        self.pdb_file: Optional[Path] = None
        self.top_file: Optional[Path] = None

    @staticmethod
    def write_pdb_frame(
        pdb_file: Path, dcd_file: Path, frame: int, output_pdb_file: Path
    ) -> None:
        mda_u = MDAnalysis.Universe(str(pdb_file), str(dcd_file))
        mda_u.trajectory[frame]
        mda_u.atoms.write(str(output_pdb_file))

    def init_simulation(self, pdb_file: Path, top_file: Optional[Path]) -> None:
        """Initialize and cache a simulation object."""
        if self.sim is not None:
            del self.sim

        self.sim = configure_simulation(
            pdb_file=pdb_file,
            top_file=top_file,
            solvent_type=self.config.solvent_type,
            gpu_index=0,
            dt_ps=self.config.dt_ps,
            temperature_kelvin=self.config.temperature_kelvin,
            heat_bath_friction_coef=self.config.heat_bath_friction_coef,
        )

    def init_continue_simulation(self, simulation_start: ContinueSimulation) -> None:
        # Use cached values
        if self.sim is None:
            raise RuntimeError("Tried to continue a simulation that doesn't exist.")
        assert self.pdb_file is not None
        self.pdb_file = self.copy_to_workdir(self.pdb_file)
        self.top_file = self.copy_to_workdir(self.top_file)

    def init_simulation_from_pdb(self, simulation_start: SimulationFromPDB) -> None:
        self.pdb_file = self.copy_to_workdir(simulation_start.pdb_file)
        self.top_file = self.copy_to_workdir(simulation_start.top_file)
        assert self.pdb_file is not None
        self.init_simulation(self.pdb_file, self.top_file)

    def init_simulation_from_restart(
        self, simulation_start: SimulationFromRestart
    ) -> None:
        sim_dir = simulation_start.sim_dir
        frame = simulation_start.sim_frame

        # Collect PDB and DCD files from previous simulation
        old_pdb_file = next(sim_dir.glob("*.pdb"))
        dcd_file = next(sim_dir.glob("*.dcd"))

        # Check for optional topology files
        top_file = next(sim_dir.glob("*.top"), None)
        if top_file is None:
            top_file = next(sim_dir.glob("*.prmtop"), None)
        self.top_file = self.copy_to_workdir(top_file)

        # New pdb file to write, example: run-<uuid>_frame000000.pdb
        pdb_name = f"{old_pdb_file.parent.name}_frame{frame:06}.pdb"
        self.pdb_file = self.workdir / pdb_name
        self.write_pdb_frame(old_pdb_file, dcd_file, frame, self.pdb_file)
        self.init_simulation(self.pdb_file, self.top_file)

    def initialize_run(self, simulation_start: SimulationStartType) -> None:
        if isinstance(simulation_start, ContinueSimulation):
            self.init_continue_simulation(simulation_start)
        elif isinstance(simulation_start, SimulationFromPDB):
            self.init_simulation_from_pdb(simulation_start)
        else:
            assert isinstance(simulation_start, SimulationFromRestart)
            self.init_simulation_from_restart(simulation_start)

    def run(self, input_data: MDSimulationInput) -> MDSimulationOutput:
        # Initialize self.sim, self.pdb_file, and self.top_file given new inputs
        self.initialize_run(input_data.simulation_start)

        # openmm typed variables
        dt_ps = self.config.dt_ps * u.picoseconds
        report_interval_ps = self.config.report_interval_ps * u.picoseconds
        simulation_length_ns = self.config.simulation_length_ns * u.nanoseconds

        # Steps between reporting DCD frames and logs
        report_steps = int(report_interval_ps / dt_ps)
        # Number of steps to run each simulation
        nsteps = int(simulation_length_ns / dt_ps)

        # Set up reporters to write simulation trajectory file and logs
        traj_file = self.workdir / "sim.dcd"
        self.sim.reporters = []  # Clear out cached reporters from previous run
        self.sim.reporters.append(app.DCDReporter(traj_file, report_steps))
        self.sim.reporters.append(
            app.StateDataReporter(
                self.workdir / "sim.log",
                report_steps,
                step=True,
                time=True,
                speed=True,
                potentialEnergy=True,
                temperature=True,
                totalEnergy=True,
            )
        )

        # Run simulation
        self.sim.step(nsteps)

        # Analyze simulation and collect contact maps and RMSD to native state
        assert self.pdb_file is not None
        contact_maps, rmsds = self.analyze_simulation(self.pdb_file, traj_file)

        # Save simulation analysis
        np.save(self.workdir / "contact_map.npy", contact_maps)
        np.save(self.workdir / "rmsd.npy", rmsds)

        # Return simulation analysis outputs
        return MDSimulationOutput(
            contact_map_path=self.persistent_dir / "contact_map.npy",
            rmsd_path=self.persistent_dir / "rmsd.npy",
        )

    def analyze_simulation(
        self, pdb_file: Path, traj_file: Path
    ) -> Tuple["npt.ArrayLike", "npt.ArrayLike"]:
        """Analyze trajectory and return a contact map and
        the RMSD to native state for each frame."""

        # Compute contact maps, rmsd, etc in bulk
        mda_u = MDAnalysis.Universe(str(pdb_file), str(traj_file))
        ref_u = MDAnalysis.Universe(str(self.config.rmsd_reference_pdb))
        # Align trajectory to compute accurate RMSD
        align.AlignTraj(
            mda_u, ref_u, select=self.config.mda_selection, in_memory=True
        ).run()
        # Get atomic coordinates of reference atoms
        ref_positions = ref_u.select_atoms(self.config.mda_selection).positions.copy()
        atoms = mda_u.select_atoms(self.config.mda_selection)
        box = mda_u.atoms.dimensions
        rows, cols, rmsds = [], [], []
        for _ in mda_u.trajectory:
            positions = atoms.positions
            # Compute contact map of current frame (scipy lil_matrix form)
            cm = distances.contact_matrix(
                positions, self.config.cutoff_angstrom, box=box, returntype="sparse"
            )
            coo = cm.tocoo()
            rows.append(coo.row.astype("int16"))
            cols.append(coo.col.astype("int16"))

            # Compute RMSD
            rmsd = rms.rmsd(positions, ref_positions, center=True, superposition=True)
            rmsds.append(rmsd)

        # Save simulation analysis results
        contact_maps = [np.concatenate(row_col) for row_col in zip(rows, cols)]

        return contact_maps, rmsds


class MockMDSimulationApplication(Application):
    def __init__(self, config: MDSimulationSettings) -> None:
        super().__init__(config)
        time.sleep(5.0)  # Emulate a large startup cost

    def run(self, input_data: MDSimulationInput) -> MDSimulationOutput:
        (self.workdir / "contact_map.npy").touch()
        (self.workdir / "rmsd.npy").touch()

        return MDSimulationOutput(
            contact_map_path=self.persistent_dir / "contact_map.npy",
            rmsd_path=self.persistent_dir / "rmsd.npy",
        )


if __name__ == "__main__":
    args = parse_application_args()
    config = MDSimulationSettings.from_yaml(args.config)
    if args.test:
        application = MockMDSimulationApplication(config)
    else:
        application = MDSimulationApplication(config)
    application.start()
