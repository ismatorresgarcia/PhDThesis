"""
This program solves the Unidirectional Pulse Propagation Equation (UPPE) of an ultra-intense
and ultra-short laser pulse.
This program includes:
    - Diffraction (for the transverse direction).
    - Second order group velocity dispersion (GVD).
    - Nonlinear optical Kerr effect (for a third-order centrosymmetric medium).
    - Multiphotonic ionization by multiphoton absorption (MPA).

Numerical discretization: Finite Differences Method (FDM).
    - Method: Split-step Fourier Crank-Nicolson (FCN) scheme.
        *- Fast Fourier Transform (FFT) scheme (for diffraction).
        *- Extended Crank-Nicolson (CN) scheme (for diffraction, Kerr and MPA).
    - Initial condition: Gaussian.
    - Boundary conditions: Neumann-Dirichlet (radial) and Periodic (temporal).

UPPE:           ∂E/∂z = i/(2k) ∇²E - ik''/2 ∂²E/∂t² + ik_0n_2|E|^2 E - iB_K|E|^(2K-2)E

DISCLAIMER: UPPE uses "god-like" units, where envelope intensity and its square module are the same.
            This is equivalent to setting 0.5*c*e_0*n_0 = 1 in the UPPE when using the SI system.
            The result obtained is identical since the consistency is mantained throught the code.
            This way, the number of operations is reduced, and the code is more readable.
            However, the dictionary "MEDIA" has an entry "INT_FACTOR" where the conversion 
            factor can be changed at will between the two unit systems.

E: envelope.
i: imaginary unit.
r: radial coordinate.
z: distance coordinate.
t: time coordinate.
k: wavenumber (in the interacting media).
k_0: wavenumber (in vacuum).
n_2: nonlinear refractive index (for a third-order centrosymmetric medium).
B_K: nonlinear multiphoton absorption coefficient.
∇: nabla operator (for the tranverse direction).
∇²: laplace operator (for the transverse direction).
"""

import numpy as np
from numpy.fft import fft, ifft
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
    # wnum = bpm["WAVENUMBER"]
    # f = bpm["FOCAL_LENGTH"]
    pkt = bpm["PEAK_TIME"]
    ch = bpm["CHIRP"]
    gauss = ampli * np.exp(
        -((r / waist) ** 2)
        # - 0.5 * imu * wnum * r**2 / f
        - (1 + imu * ch) * (t / pkt) ** 2
    )

    return gauss


def crank_nicolson_diags(n, pos, coor, coef):
    """
    Generate the three diagonals for a Crank-Nicolson array with centered differences.

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


def crank_nicolson_array(n, pos, coor, coef):
    """
    Generate a Crank-Nicolson sparse array in CSR format using the diagonals.

    Parameters:
    - n (int): Number of radial nodes
    - pos (str): Position of the Crank-Nicolson array (left or right)
    - coor (int): Parameter for planar (0) or cylindrical (1) geometry
    - coef (float): Coefficient for the diagonal elements

    Returns:
    - array: Containing the Crank-Nicolson sparse array in CSR format
    """
    diag_m1, diag_0, diag_p1 = crank_nicolson_diags(n, pos, coor, coef)

    diags = [diag_m1, diag_0, diag_p1]
    offset = [-1, 0, 1]
    cn_array = diags_array(diags, offsets=offset, format="csr")

    return cn_array


IM_UNIT = 1j
PI = np.pi

LIGHT_SPEED = 299792458
PERMITTIVITY = 8.8541878128e-12
LIN_REF_IND_WATER = 1.334
NLIN_REF_IND_WATER = 1.6e-20
GVD_COEF_WATER = 241e-28
N_PHOTONS_WATER = 5
BETA_COEF_WATER = 8e-61

WAVELENGTH_0 = 800e-9
WAIST_0 = 100e-6
PEAK_TIME = 130e-15
ENERGY = 2.2e-6
FOCAL_LENGTH = 20
CHIRP = -1

# INT_FACTOR = 0.5 * LIGHT_SPEED * PERMITTIVITY * LIN_REF_IND_WATER
INT_FACTOR = 1
WAVENUMBER_0 = 2 * PI / WAVELENGTH_0
WAVENUMBER = 2 * PI * LIN_REF_IND_WATER / WAVELENGTH_0
POWER = ENERGY / (PEAK_TIME * np.sqrt(0.5 * PI))
CR_POWER = 3.77 * WAVELENGTH_0**2 / (8 * PI * LIN_REF_IND_WATER * NLIN_REF_IND_WATER)
INTENSITY = 2 * POWER / (PI * WAIST_0**2)
AMPLITUDE = np.sqrt(INTENSITY / INT_FACTOR)

MPA_EXP = 2 * N_PHOTONS_WATER - 2
KERR_COEF = IM_UNIT * WAVENUMBER_0 * NLIN_REF_IND_WATER * INT_FACTOR
MPA_COEF = -0.5 * BETA_COEF_WATER * INT_FACTOR ** (N_PHOTONS_WATER - 1)

MEDIA = {
    "WATER": {
        "LIN_REF_IND": LIN_REF_IND_WATER,
        "NLIN_REF_IND": NLIN_REF_IND_WATER,
        "GVD_COEF": GVD_COEF_WATER,
        "N_PHOTONS": N_PHOTONS_WATER,  # Number of photons absorbed [-]
        "BETA_COEF": BETA_COEF_WATER,  # MPA coefficient [m(2K-3) / W-(K-1)]
        "MPA_EXP": MPA_EXP,  # MPA exponent [-]
        "KERR_COEF": KERR_COEF,  # Kerr coefficient [m^2 / W]
        "MPA_COEF": MPA_COEF,  # MPA coefficient [m^2 / W]
        "INT_FACTOR": INT_FACTOR,
    },
    "VACUUM": {
        "LIGHT_SPEED": 299792458,
        "PERMITTIVITY": 8.8541878128e-12,
    },
}

BEAM = {
    "WAVELENGTH_0": WAVELENGTH_0,
    "WAIST_0": WAIST_0,
    "PEAK_TIME": PEAK_TIME,
    "ENERGY": ENERGY,
    "FOCAL_LENGTH": FOCAL_LENGTH,
    "CHIRP": CHIRP,
    "WAVENUMBER_0": WAVENUMBER_0,
    "WAVENUMBER": WAVENUMBER,
    "POWER": POWER,
    "CR_POWER": CR_POWER,
    "INTENSITY": INTENSITY,
    "AMPLITUDE": AMPLITUDE,
}

## Set parameters (grid spacing, propagation step, etc.)
# Radial (r) grid
INI_RADI_COOR, FIN_RADI_COOR, I_RADI_NODES = 0, 25e-4, 1500
N_RADI_NODES = I_RADI_NODES + 2
RADI_STEP_LEN = (FIN_RADI_COOR - INI_RADI_COOR) / (N_RADI_NODES - 1)
AXIS_NODE = int(-INI_RADI_COOR / RADI_STEP_LEN)  # On-axis node
# Propagation (z) grid
INI_DIST_COOR, FIN_DIST_COOR, N_STEPS = 0, 2e-2, 1000
DIST_STEP_LEN = FIN_DIST_COOR / N_STEPS
# Time (t) grid
INI_TIME_COOR, FIN_TIME_COOR, N_TIME_NODES = -200e-15, 200e-15, 4096
TIME_STEP_LEN = (FIN_TIME_COOR - INI_TIME_COOR) / (N_TIME_NODES - 1)
PEAK_NODE = N_TIME_NODES // 2  # Peak intensity node
# Angular frequency (ω) grid
FRQ_STEP_LEN = 2 * PI / (N_TIME_NODES * TIME_STEP_LEN)
INI_FRQ_COOR_W1 = 0
FIN_FRQ_COOR_W1 = PI / TIME_STEP_LEN - FRQ_STEP_LEN
INI_FRQ_COOR_W2 = -PI / TIME_STEP_LEN
FIN_FRQ_COOR_W2 = -FRQ_STEP_LEN
w1 = np.linspace(INI_FRQ_COOR_W1, FIN_FRQ_COOR_W1, N_TIME_NODES // 2)
w2 = np.linspace(INI_FRQ_COOR_W2, FIN_FRQ_COOR_W2, N_TIME_NODES // 2)
radi_array = np.linspace(INI_RADI_COOR, FIN_RADI_COOR, N_RADI_NODES)
dist_array = np.linspace(INI_DIST_COOR, FIN_DIST_COOR, N_STEPS + 1)
time_array = np.linspace(INI_TIME_COOR, FIN_TIME_COOR, N_TIME_NODES)
frq_array = np.append(w1, w2)
radi_2d_array, dist_2d_array = np.meshgrid(radi_array, dist_array, indexing="ij")
radi_2d_array_2, time_2d_array_2 = np.meshgrid(radi_array, time_array, indexing="ij")
dist_2d_array_3, time_2d_array_3 = np.meshgrid(dist_array, time_array, indexing="ij")

## Set loop variables
EU_CYL = 1  # Parameter for planar (0) or cylindrical (1) geometry
DELTA_R = 0.25 * DIST_STEP_LEN / (BEAM["WAVENUMBER"] * RADI_STEP_LEN**2)
DELTA_T = -0.25 * DIST_STEP_LEN * MEDIA["WATER"]["GVD_COEF"] / TIME_STEP_LEN**2
envelope = np.empty_like(radi_2d_array_2, dtype=complex)
envelope_axis = np.empty_like(dist_2d_array_3, dtype=complex)
envelope_fourier = np.empty_like(time_array, dtype=complex)
envelope_store = np.empty_like(envelope)
fourier_coeff = np.exp(-2 * IM_UNIT * DELTA_T * (frq_array * TIME_STEP_LEN) ** 2)
b_array = np.empty_like(envelope)
c_array = np.empty([N_RADI_NODES, N_TIME_NODES, 3], dtype=complex)
d_array = np.empty_like(radi_array)
f_array = np.empty_like(radi_array)
w_array = np.empty([N_RADI_NODES, N_TIME_NODES, 2], dtype=complex)

## Set tridiagonal Crank-Nicolson matrices in csr_array format
MATRIX_CNT_1 = IM_UNIT * DELTA_R
left_cn_matrix = crank_nicolson_array(N_RADI_NODES, "LEFT", EU_CYL, MATRIX_CNT_1)
right_cn_matrix = crank_nicolson_array(N_RADI_NODES, "RIGHT", EU_CYL, -MATRIX_CNT_1)

## Set initial electric field wave packet
envelope = initial_condition(radi_2d_array_2, time_2d_array_2, IM_UNIT, BEAM)
# Save on-axis envelope initial state
envelope_axis[0, :] = envelope[AXIS_NODE, :]

## Propagation loop over desired number of steps
for k in tqdm(range(N_STEPS - 1)):
    # Compute first half-step (Spectral domain)
    for i in range(N_RADI_NODES):
        envelope_fourier = fourier_coeff * fft(envelope[i, :])
        # Compute first half-step solution
        b_array[i, :] = ifft(envelope_fourier)

    # Compute second half-step (Time domain)
    for l in range(N_TIME_NODES):
        c_array[:, l, 0] = b_array[:, l]
        c_array[:, l, 1] = np.abs(c_array[:, l, 0]) ** 2
        c_array[:, l, 2] = np.abs(c_array[:, l, 0]) ** MEDIA["WATER"]["MPA_EXP"]
        if k == 0:  # I'm guessing a value for starting the AB2 method
            w_array[:, l, 0] = (
                DIST_STEP_LEN
                * (
                    MEDIA["WATER"]["KERR_COEF"] * c_array[:, l, 1]
                    + MEDIA["WATER"]["MPA_COEF"] * c_array[:, l, 2]
                )
                * c_array[:, l, 0]
            )
            G = 1.0
            c_array[:, l, 0] = G * c_array[:, l, 0]
            c_array[:, l, 1] = np.abs(c_array[:, l, 0]) ** 2
            c_array[:, l, 2] = np.abs(c_array[:, l, 0]) ** MEDIA["WATER"]["MPA_EXP"]
            w_array[:, l, 1] = (
                DIST_STEP_LEN
                * (
                    MEDIA["WATER"]["KERR_COEF"] * c_array[:, l, 1]
                    + MEDIA["WATER"]["MPA_COEF"] * c_array[:, l, 2]
                )
                * c_array[:, l, 0]
            )
            envelope_axis[k + 1, l] = c_array[AXIS_NODE, l, 0]
        else:
            w_array[:, l, 1] = (
                DIST_STEP_LEN
                * (
                    MEDIA["WATER"]["KERR_COEF"] * c_array[:, l, 1]
                    + MEDIA["WATER"]["MPA_COEF"] * c_array[:, l, 2]
                )
                * c_array[:, l, 0]
            )

        # Compute intermediate arrays
        d_array = right_cn_matrix @ c_array[:, l, 0]
        f_array = d_array + 0.5 * (3 * w_array[:, l, 1] - w_array[:, l, 0])

        # Compute second half-step solution
        envelope_store[:, l] = spsolve(left_cn_matrix, f_array)

    # Update arrays for the next step
    w_array[:, :, 0] = w_array[:, :, 1]
    envelope = envelope_store
    envelope_axis[k + 2, :] = envelope_store[AXIS_NODE, :]

np.savez(
    "/Users/ytoga/projects/phd_thesis/phd_coding/python/storage/ffdmk_fcn_1",
    INI_RADI_COOR=INI_RADI_COOR,
    FIN_RADI_COOR=FIN_RADI_COOR,
    INI_DIST_COOR=INI_DIST_COOR,
    FIN_DIST_COOR=FIN_DIST_COOR,
    INI_TIME_COOR=INI_TIME_COOR,
    FIN_TIME_COOR=FIN_TIME_COOR,
    AXIS_NODE=AXIS_NODE,
    PEAK_NODE=PEAK_NODE,
    LIN_REF_IND=MEDIA["WATER"]["LIN_REF_IND"],
    e=envelope,
    e_axis=envelope_axis,
)
