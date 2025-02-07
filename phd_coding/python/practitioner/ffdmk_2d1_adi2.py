"""
This program solves the Unidirectional Pulse Propagation Equation (UPPE) of an ultra-intense
and ultra-short laser pulse.
This program includes:
    - Diffraction (for the transverse direction).
    - Second order group velocity dispersion (GVD).
    - Nonlinear optical Kerr effect (for a third-order centrosymmetric medium).
    - Multiphotonic ionization by multiphoton absorption (MPA).

Numerical discretization: Finite Differences Method (FDM)
    - Method: Extended Alternating Direction Implicit (ADI) scheme
        *- Alternating Direction Implicit (ADI) scheme (for diffraction and GVD)
        *- 2-step Adams-Bashforth (AB) scheme (for MPA and Kerr)
    - Initial condition: Gaussian
    - Boundary conditions: Neumann-Dirichlet (radial) and homogeneous Dirichlet (temporal)

UPPE:           ∂E/∂z = i/(2k) ∇²E - ik''/2 ∂²E/∂t² + ik_0n_2|E|^2 E - iB_K|E|^(2K-2)E

DISCLAIMER: UPPE uses "god-like" units, where envelope intensity and its square module are the same.
            This is equivalent to setting 0.5*c*e_0*n_0 = 1 in the UPPE when using the SI system.
            The result obtained is identical since the consistency is mantained throught the code.
            This way, the number of operations is reduced, and the code is more readable.
            However, the dictionary "MEDIA" has an entry "INT_FACTOR" where the conversion 
            factor can be changed at will between the two unit systems.

E: envelope
i: imaginary unit
r: radial coordinate
z: distance coordinate
t: time coordinate
k: wavenumber (in the interacting media)
k_0: wavenumber (in vacuum)
n_2: nonlinear refractive index (for a third-order centrosymmetric medium)
B_K: nonlinear multiphoton absorption coefficient
∇: nabla operator (for the tranverse direction)
∇²: laplace operator (for the transverse direction)
"""

import numpy as np
from scipy.sparse import diags_array
from scipy.sparse.linalg import spsolve
from tqdm import tqdm


def initial_condition(r, t, imu, bpm):
    """
    Set the post-lens chirped Gaussian beam.

    Parameters:
    - r (array): Radial array
    - t (array): Time array
    - imu (complex): Square root of -1
    - bpm (dict): Dictionary containing the beam parameters
        - ampli (float): Amplitude of the Gaussian beam
        - waist (float): Waist of the Gaussian beam
        - wnum (float): Wavenumber of the Gaussian beam
        - f (float): Focal length of the initial lens
        - pkt (float): Time at which the Gaussian beam reaches its peak intensity
        - ch (float): Initial chirping introduced by some optical system
    """
    ampli = bpm["AMPLITUDE"]
    waist = bpm["WAIST_0"]
    wnum = bpm["WAVENUMBER"]
    f = bpm["FOCAL_LENGTH"]
    pkt = bpm["PEAK_TIME"]
    ch = bpm["CHIRP"]
    gauss = ampli * np.exp(
        -((r / waist) ** 2)
        - 0.5 * imu * wnum * r**2 / f
        - (1 + imu * ch) * (t / pkt) ** 2
    )

    return gauss


def crank_nicolson_diags_r(n, pos, coor, coef):
    """
    Generate the three diagonals for a Crank-Nicolson radial array with centered differences.

    Parameters:
    - n (int): Number of radial nodes
    - pos (str): Position of the Crank-Nicolson array (left or right)
    - coor (int): Parameter for planar (0) or cylindrical (1) geometry
    - coef (float): Coefficient for the diagonal elements

    Returns:
    - tuple: Containing the upper, main, and lower diagonals
    """
    mcf = 1 + 2 * coef
    ind = np.arange(1, n - 1)

    diag_m1 = -coef * (1 - 0.5 * coor / ind)
    diag_0 = np.full(n, mcf)
    diag_p1 = -coef * (1 + 0.5 * coor / ind)

    diag_m1 = np.append(diag_m1, [0])
    diag_p1 = np.insert(diag_p1, 0, [0])
    if coor == 0 and pos == "LEFT":
        diag_0[0], diag_0[-1] = 1, 1
    elif coor == 0 and pos == "RIGHT":
        diag_0[0], diag_0[-1] = 0, 0
    elif coor == 1 and pos == "LEFT":
        diag_0[0], diag_0[-1] = mcf, 1
        diag_p1[0] = -2 * coef
    elif coor == 1 and pos == "RIGHT":
        diag_0[0], diag_0[-1] = mcf, 0
        diag_p1[0] = -2 * coef

    return diag_m1, diag_0, diag_p1


def crank_nicolson_diags_t(n, pos, coef):
    """
    Set the three diagonals for a Crank-Nicolson time array with centered differences.

    Parameters:
    - n (int): Number of time nodes
    - pos (str): Position of the Crank-Nicolson array (left or right)
    - coef (float): Coefficient for the diagonal elements

    Returns:
    - tuple: Containing the upper, main, and lower diagonals
    """
    mcf = 1 + 2 * coef

    diag_m1 = np.full(n - 1, -coef)
    diag_0 = np.full(n, mcf)
    diag_p1 = np.full(n - 1, -coef)

    diag_p1[0], diag_m1[-1] = 0, 0
    if pos == "LEFT":
        diag_0[0], diag_0[-1] = 1, 1
    elif pos == "RIGHT":
        diag_0[0], diag_0[-1] = 0, 0

    return diag_m1, diag_0, diag_p1


def crank_nicolson_array_r(n, pos, coor, coef):
    """
    Generate a Crank-Nicolson radial sparse array in CSR format using the diagonals.

    Parameters:
    - n (int): Number of radial nodes
    - pos (str): Position of the Crank-Nicolson array (left or right)
    - coor (int): Parameter for planar (0) or cylindrical (1) geometry
    - coef (float): Coefficient for the diagonal elements

    Returns:
    - array: Containing the Crank-Nicolson sparse array in CSR format
    """
    diag_m1, diag_0, diag_p1 = crank_nicolson_diags_r(n, pos, coor, coef)

    diags = [diag_m1, diag_0, diag_p1]
    offset = [-1, 0, 1]
    cn_array = diags_array(diags, offsets=offset, format="csr")

    return cn_array


def crank_nicolson_array_t(n, pos, coef):
    """
    Set a Crank-Nicolson sparse time array in CSR format using the diagonals.

    Parameters:
    - n (int): Number of time nodes
    - pos (str): Position of the Crank-Nicolson array (left or right)
    - coef (float): Coefficient for the diagonal elements

    Returns:
    - array: Containing the Crank-Nicolson sparse array in CSR format
    """
    diag_m1, diag_0, diag_p1 = crank_nicolson_diags_t(n, pos, coef)

    diags = [diag_m1, diag_0, diag_p1]
    offset = [-1, 0, 1]
    cn_array = diags_array(diags, offsets=offset, format="csr")

    return cn_array


IM_UNIT = 1j
PI = np.pi

MEDIA = {
    "WATER": {
        "LIN_REF_IND": 1.334,
        "NLIN_REF_IND": 1.6e-20,
        "GVD_COEF": 241e-28,
        "N_PHOTONS": 5,  # Number of photons absorbed [-]
        "BETA_COEF": 1e-61,  # MPA coefficient [m(2K-3) / W-(K-1)]
    },
    "VACUUM": {
        "LINEAR_REFF_INDEX": 1,
        "LIGHT_SPEED": 299792458,
        "PERMITTIVITY": 8.8541878128e-12,
    },
}
BEAM = {
    "WAVELENGTH_0": 800e-9,
    "WAIST_0": 75e-5,
    "PEAK_TIME": 130e-15,
    "ENERGY": 2.2e-6,
    "FOCAL_LENGTH": 1000,
    "CHIRP": -1,
}
MEDIA["WATER"].update(
    {
        # "INT_FACTOR": 0.5
        # * MEDIA["VACUUM"]["LIGHT_SPEED"]
        # * MEDIA["VACUUM"]["PERMITTIVITY"]
        # * MEDIA["WATER"]["LIN_REF_IND"],
        "INT_FACTOR": 1,
    }
)
BEAM.update(
    {
        "WAVENUMBER_0": 2 * PI / BEAM["WAVELENGTH_0"],
        "WAVENUMBER": 2 * PI * MEDIA["WATER"]["LIN_REF_IND"] / BEAM["WAVELENGTH_0"],
        "POWER": BEAM["ENERGY"] / (BEAM["PEAK_TIME"] * np.sqrt(0.5 * PI)),
        "CR_POWER": 3.77
        * BEAM["WAVELENGTH_0"] ** 2
        / (8 * PI * MEDIA["WATER"]["LIN_REF_IND"] * MEDIA["WATER"]["NLIN_REF_IND"]),
    }
)
MEDIA["WATER"].update(
    {
        "MPA_EXP": 2 * MEDIA["WATER"]["N_PHOTONS"] - 2,
        "KERR_COEF": IM_UNIT
        * BEAM["WAVENUMBER_0"]
        * MEDIA["WATER"]["NLIN_REF_IND"]
        * MEDIA["WATER"]["INT_FACTOR"],
    }
)
MEDIA["WATER"].update(
    {
        "MPA_COEF": -0.5
        * MEDIA["WATER"]["BETA_COEF"]
        * MEDIA["WATER"]["INT_FACTOR"] ** (MEDIA["WATER"]["N_PHOTONS"] - 1)
    }
)
BEAM.update({"INTENSITY": 2 * BEAM["POWER"] / (PI * BEAM["WAIST_0"] ** 2)})
BEAM.update({"AMPLITUDE": np.sqrt(BEAM["INTENSITY"] / MEDIA["WATER"]["INT_FACTOR"])})

## Set parameters (grid spacing, propagation step, etc.)
# Radial (r) grid
INI_RADI_COOR, FIN_RADI_COOR, I_RADI_NODES = 0, 75e-4, 200
N_RADI_NODES = I_RADI_NODES + 2
RADI_STEP_LEN = (FIN_RADI_COOR - INI_RADI_COOR) / (N_RADI_NODES - 1)
AXIS_NODE = int(-INI_RADI_COOR / RADI_STEP_LEN)  # On-axis node
# Propagation (z) grid
INI_DIST_COOR, FIN_DIST_COOR, N_STEPS = 0, 6e-2, 300
DIST_STEP_LEN = FIN_DIST_COOR / N_STEPS
# Time (t) grid
INI_TIME_COOR, FIN_TIME_COOR, I_TIME_NODES = -300e-15, 300e-15, 1024
N_TIME_NODES = I_TIME_NODES + 2
TIME_STEP_LEN = (FIN_TIME_COOR - INI_TIME_COOR) / (N_TIME_NODES - 1)
PEAK_NODE = N_TIME_NODES // 2  # Peak intensity node
radi_array = np.linspace(INI_RADI_COOR, FIN_RADI_COOR, N_RADI_NODES)
dist_array = np.linspace(INI_DIST_COOR, FIN_DIST_COOR, N_STEPS + 1)
time_array = np.linspace(INI_TIME_COOR, FIN_TIME_COOR, N_TIME_NODES)
radi_2d_array, dist_2d_array = np.meshgrid(radi_array, dist_array, indexing="ij")
radi_2d_array_2, time_2d_array_2 = np.meshgrid(radi_array, time_array, indexing="ij")
dist_2d_array_3, time_2d_array_3 = np.meshgrid(dist_array, time_array, indexing="ij")

## Set loop variables
EU_CYL = 1  # Parameter for planar (0) or cylindrical (1) geometry
DELTA_R = 0.25 * DIST_STEP_LEN / (BEAM["WAVENUMBER"] * RADI_STEP_LEN**2)
DELTA_T = -0.25 * DIST_STEP_LEN * MEDIA["WATER"]["GVD_COEF"] / TIME_STEP_LEN**2
envelope = np.empty_like(radi_2d_array_2, dtype=complex)
envelope_axis = np.empty_like(dist_2d_array_3, dtype=complex)
envelope_store = np.empty_like(envelope)
b_array = np.empty_like(envelope)
c_array = np.empty_like(envelope)
d_array = np.empty([N_RADI_NODES, N_TIME_NODES, 3], dtype=complex)
f_array = np.empty_like(envelope)
w_array = np.empty([N_RADI_NODES, N_TIME_NODES, 2], dtype=complex)

## Set tridiagonal Crank-Nicolson matrices in csr_array format
MAT_CNT_1R = IM_UNIT * DELTA_R
MAT_CNT_1T = IM_UNIT * DELTA_T
left_cn_matrix_r = crank_nicolson_array_r(N_RADI_NODES, "LEFT", EU_CYL, MAT_CNT_1R)
right_cn_matrix_r = crank_nicolson_array_r(N_RADI_NODES, "RIGHT", EU_CYL, -MAT_CNT_1R)
left_cn_matrix_t = crank_nicolson_array_t(N_TIME_NODES, "LEFT", MAT_CNT_1T)
right_cn_matrix_t = crank_nicolson_array_t(N_TIME_NODES, "RIGHT", -MAT_CNT_1T)

## Set initial electric field wave packet
envelope = initial_condition(radi_2d_array_2, time_2d_array_2, IM_UNIT, BEAM)
# Save on-axis envelope initial state
envelope_axis[0, :] = envelope[AXIS_NODE, :]

## Propagation loop over desired number of steps
for k in tqdm(range(N_STEPS)):
    ## Compute first half-step (ADI transverse direction)
    # Compute right-hand side matrix product row by row
    for i in range(N_RADI_NODES):
        b_array[i, :] = right_cn_matrix_t @ envelope[i, :]

    # Compute first half-step solution
    for l in range(N_TIME_NODES):
        d_array[:, l, 0] = envelope[:, l]
        d_array[:, l, 1] = np.abs(d_array[:, l, 0]) ** 2
        d_array[:, l, 2] = np.abs(d_array[:, l, 0]) ** MEDIA["WATER"]["MPA_EXP"]
        if k == 0:  # I'm guessing a value for starting the AB2 method
            w_array[:, l, 0] = (
                DIST_STEP_LEN
                * (
                    MEDIA["WATER"]["KERR_COEF"] * d_array[:, l, 1]
                    + MEDIA["WATER"]["MPA_COEF"] * d_array[:, l, 2]
                )
                * d_array[:, l, 0]
            )
            G = 1.0
            d_array[:, l, 0] = G * d_array[:, l, 0]
            d_array[:, l, 1] = np.abs(d_array[:, l, 0]) ** 2
            d_array[:, l, 2] = np.abs(d_array[:, l, 0]) ** MEDIA["WATER"]["MPA_EXP"]
            w_array[:, l, 1] = (
                DIST_STEP_LEN
                * (
                    MEDIA["WATER"]["KERR_COEF"] * d_array[:, l, 1]
                    + MEDIA["WATER"]["MPA_COEF"] * d_array[:, l, 2]
                )
                * d_array[:, l, 0]
            )
            envelope_axis[k + 1, l] = d_array[AXIS_NODE, l, 0]
        else:
            w_array[:, l, 1] = (
                DIST_STEP_LEN
                * (
                    MEDIA["WATER"]["KERR_COEF"] * d_array[:, l, 1]
                    + MEDIA["WATER"]["MPA_COEF"] * d_array[:, l, 2]
                )
                * d_array[:, l, 0]
            )

        # Compute intermediate arrays
        f_array[:, l] = b_array[:, l] + 0.25 * (3 * w_array[:, l, 1] - w_array[:, l, 0])

        # Compute first half-step solution
        c_array[:, l] = spsolve(left_cn_matrix_r, f_array[:, l])

    # Update arrays for the next half-step
    w_array[:, :, 0] = w_array[:, :, 1]

    ## Compute second half-step (ADI time direction)
    # Compute right-hand side matrix product column by column
    for l in range(N_TIME_NODES):
        b_array[:, l] = right_cn_matrix_r @ c_array[:, l]

    # Compute second half-step solution
    for i in range(N_RADI_NODES):
        d_array[i, :, 0] = c_array[i, :]
        d_array[i, :, 1] = np.abs(d_array[i, :, 0]) ** 2
        d_array[i, :, 2] = np.abs(d_array[i, :, 0]) ** MEDIA["WATER"]["MPA_EXP"]
        if k == 0:  # I'm guessing a value for starting the AB2 method
            w_array[i, :, 0] = (
                DIST_STEP_LEN
                * (
                    MEDIA["WATER"]["KERR_COEF"] * d_array[i, :, 1]
                    + MEDIA["WATER"]["MPA_COEF"] * d_array[i, :, 2]
                )
                * d_array[i, :, 0]
            )
            G = 1.0
            d_array[i, :, 0] = G * d_array[i, :, 0]
            d_array[i, :, 1] = np.abs(d_array[i, :, 0]) ** 2
            d_array[i, :, 2] = np.abs(d_array[i, :, 0]) ** MEDIA["WATER"]["MPA_EXP"]
            if i == AXIS_NODE:
                envelope_axis[k + 1, :] = d_array[i, :, 0]
        else:
            w_array[i, :, 1] = (
                DIST_STEP_LEN
                * (
                    MEDIA["WATER"]["KERR_COEF"] * d_array[i, :, 1]
                    + MEDIA["WATER"]["MPA_COEF"] * d_array[i, :, 2]
                )
                * d_array[i, :, 0]
            )

        # Compute intermediate arrays
        f_array[i, :] = b_array[i, :] + 0.25 * (3 * w_array[i, :, 1] - w_array[i, :, 0])

        # Compute second half-step solution
        envelope_store[i, :] = spsolve(left_cn_matrix_t, f_array[i, :])

    # Update arrays for the next step
    w_array[:, :, 0] = w_array[:, :, 1]
    envelope = envelope_store
    envelope_axis[k + 1, :] = envelope_store[AXIS_NODE, :]

np.savez(
    "/Users/ytoga/projects/phd_thesis/phd_coding/python/storage/ffdmk_adi2_1",
    INI_RADI_COOR=INI_RADI_COOR,
    FIN_RADI_COOR=FIN_RADI_COOR,
    INI_DIST_COOR=INI_DIST_COOR,
    FIN_DIST_COOR=FIN_DIST_COOR,
    INI_TIME_COOR=INI_TIME_COOR,
    FIN_TIME_COOR=FIN_TIME_COOR,
    AXIS_NODE=AXIS_NODE,
    PEAK_NODE=PEAK_NODE,
    LIN_REF_INDEX=MEDIA["WATER"]["LIN_REF_IND"],
    e=envelope,
    e_axis=envelope_axis,
)
