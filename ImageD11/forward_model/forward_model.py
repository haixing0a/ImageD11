# forward_model.py
# 1) Given omega, Gw, pos, UBI etc + parameters, output the expected intersection position on the detector
# Computation done in lab frame: x along X-ray beam, y points outward the synchrotron horizontally, z is vertical up
# 2) filter, compare, plot etc for column file (cf)
# 3) forward_match_peaks to find matched peaks and compute completeness
# Haixing Fang, haixing.fang@esrf.fr
# Oct 4th, 2024

import numpy as np
import ImageD11.columnfile
import ImageD11.unitcell
import ImageD11.transformer
from ImageD11.forward_model import pars_conversion
from matplotlib import pyplot as plt

econst = 12.3984


def forward_match_peaks(cf_strong, grains, ds, ucell, pars, ds_max = 2.0, tol_angle=1, tol_pixel=10, thres_int = None):
    """
    Perform forward calculation to find the matched fwd peaks and exp peaks

    Arguments:
    cf_strong -- ImageD11 colume file, e.g. cf_4d = ds.get_cf_4d_from_disk() and then filter
    grains    -- list of grain object, e.g. grains = ds.get_grains_from_disk(phase_str)
    ds        -- dataset object,   e.g. ds = ImageD11.sinograms.dataset.load(dset_file)
    ucell     -- ImageD11.unitcell object, e.g. ucell = ds.phases.unitcells[phase_str]
    pars      -- ImageD1.parameters object, e.g. pars = ImageD11.parameters.read_par_file(ds.parfile)
    Optional arguments:
    ds_max    -- maximum 1/d
    tol_angle -- tolerance of angles for matching peaks
    tol_piexl -- tolerance of pixels for matching peaks
    thres_int -- intensity threshold, None as default to not remove peaks

    Returns:
    cf_matched_all -- list of matched peaks for each of the grain, list of ImageD11.columnfile
    Comp_all -- Completeness values [N*2] list, original completeness and updated completeness after removing the low-intensity matched peaks
    """
    cf_matched_all = []
    Comp_all = []

    rot_min = ds.omega.min()-0.1
    rot_max = ds.omega.max()+0.1
    rot_step = ds.omega[0][1]-ds.omega[0][0]
    for i in range(len(grains)):
        print('******************************************** Perform forward computation for grain no. {} *********************************************'.format(i))
        if grains[i].translation is None:
            pos = np.array([0.0, 0.0, 0.0])
        else:
            pos = grains[i].translation/1000.0

        U = grains[i].U
        B = grains[i].B

        fwd, Nr_simu = forward_comp(pos, U, B, ucell, pars, ds_max = ds_max, rot_start = rot_min, rot_end = rot_max, rot_step = rot_step)
        cf_matched, fwd_matched, ij, Completeness = find_matching_peaks(cf_strong, fwd, dsmax = ds_max, tol_angle=tol_angle, tol_pixel=tol_pixel)
        if thres_int is not None:
             cf_matched = cf_remove_weak_peaks(cf_matched, thres_int = thres_int)
        Comp_all.append([Completeness, cf_matched.nrows/Nr_simu])
        cf_matched_all.append(cf_matched)
    return cf_matched_all, Comp_all
    
        
    
def forward_comp(pos, U, B, ucell, pars, ds_max = 1.2, rot_start = -91, rot_end = 91, rot_step = 0.05):
    """
    Given pos, U, B, ucell, pars, forward calculate the expected intersection position on the detector.

    Arguments:
    pos -- position vector
    U -- rotation matrix
    B -- B matrix represented by lattice parameters
    ucell -- ImageD11.unitcell object
    pars -- ImageD1.parameters object, e.g. ImageD11.parameters.read_par_file('pars.json')
    ds_max -- maximum ds
    rot_start -- starting omega angle [deg]
    rot_step -- step size of the omega rotation [deg]
    rot_end -- end omega angle [deg]

    Returns:
    fwd -- list of expected peak positions: [rot in degrees, hkl, 2-theta in degrees, Gt, dety, detz, dety_mm, detz_mm, HitDetFlag]
    Nr_simu -- number of theoretical spots expected on the detector
    """    
    Nr_simu = 0
    fwd = []
    
    p = pars_conversion.convert_ImageD11pars2p(pars)
    
    wavelength = econst/p['Energy'] # [Angstrom]
    
    Ki = np.array([p['Energy']/econst, 0, 0]) # note that there is no 2*pi here
    Klen = np.linalg.norm(Ki) # [A^-1]

    ds_all, hkls = get_hkls(ucell, dsmax = ds_max)
    
    Gw = np.dot(p['S'], np.dot(U, np.dot(B, hkls.T)))
    # Glen = np.sqrt(Gw[0, :]**2 + Gw[1, :]**2 + Gw[2, :]**2)
    Glen = np.linalg.norm(Gw, axis=0)
    theta = np.arcsin(Glen / (2*Klen))
    
    a = Gw[0,:]/Glen
    b = -Gw[1,:]/Glen
    c = (np.cos(2*theta)-1)/np.sqrt(2*(1-np.cos(2*theta)))
    d = a**2 + b**2
    sqD = d - c**2
    omega_all = np.zeros((Gw.shape[1],2))
    for i in range(omega_all.shape[0]):
        omega_all[i,:] = find_omega(a[i], b[i], c[i], d[i], sqD[i])
    if rot_start < 0:
        omega_all[omega_all > (2*np.pi + np.deg2rad(rot_start))] -= 2*np.pi # bring the omega solution from [0, 2*pi] to [rot_start, 2*pi+rot_start]
        
    print('Investigating {} possible omega angle solutions ...'.format(omega_all.shape))
    print('Omega angle range: [{}, {}]'.format(np.rad2deg(np.nanmin(omega_all)), np.rad2deg(np.nanmax(omega_all))))

    # print(np.rad2deg(omega_all))
    # computation in lab system
    i = 0
    for ii in range(omega_all.shape[0]):
        for jj in range(2):
            i += 1
            omega = omega_all[ii,jj]
            rot = np.rad2deg(omega)
            if rot >= rot_start and rot < rot_end - rot_step / 2:
                dety, detz, dety_mm, detz_mm, Gt, HitDetFlag = forward_det_pos(pos, omega, Gw[:, ii], theta[ii], p)
                if HitDetFlag == True:
                    Nr_simu += 1
                fwd.append([rot, hkls[ii,:], np.rad2deg(theta[ii])*2, Gt, dety, detz, dety_mm, detz_mm, HitDetFlag])
                
    print('Done! {} peaks expected on the detector by investigating {}*2 hkl reflections for rotation angle range between {} and {} degrees'.format(Nr_simu, hkls.shape[0], rot_start, rot_end))
    print('fwd list contains: [rot in degrees, hkl, 2-theta in degrees, Gt, dety, detz, dety_mm, detz_mm, HitDetFlag]')
    return fwd, Nr_simu


def forward_det_pos(pos, omega, Gw, theta, p):
    """
    Given omega, Gw, pos, U, and other parameters, outputs the expected intersection position on the detector.

    Arguments:
    pos -- position vector
    S -- rotation matrix or similar
    omega -- rotation angle in radians
    Gw -- G vector
    p  -- parameter containing the following:
    Lsam2det -- distance from the sample to the detector
    theta -- diffraction angle
    RotDet -- rotation matrix for the detector
    dety0, detz0 -- detector center positions in pixels
    dety00, detz00 -- detector offset position
    pixelysize, pixelzsize -- pixel sizes in mm
    detysize, detzsize -- detector sizes in pixels
    BeamStopY, BeamStopZ -- beamstop positions
    flip_lr_flag, flip_ud_flag -- flags for flipping left-right or up-down

    Returns:
    dety, detz -- detector positions in pixels
    dety_mm, detz_mm -- detector positions in mm
    Gt.T -- diffraction vector in lab frame, 1*3 numpy array
    HitDetFlag -- flag if the position hits the effective area of the detector
    """
    
    omega_matrix = get_omega_matrix(omega)
    Gt = np.dot(omega_matrix, Gw)
    SamposW = np.dot(omega_matrix, np.dot(p['S'], pos.T))

    # Sample center projected to the position of the detector
    center = np.array([p['Lsam2det'] - SamposW[0], SamposW[1], SamposW[2]]).T

    # Difference vector in mm
    diffvec = (p['Lsam2det'] - SamposW[0]) * np.tan(2 * theta)
    konst = np.sqrt(Gt[1]**2 + Gt[2]**2)

    dety22 = center[1] + (diffvec * Gt[1] / konst)  # dety in mm
    detz22 = center[2] + (diffvec * Gt[2] / konst)  # detz in mm
        
    K_out_unit = (np.array([p['Lsam2det'], dety22, detz22]) - SamposW) / np.sqrt((p['Lsam2det'] - SamposW[0])**2 + (dety22 - SamposW[1])**2 + (detz22 - SamposW[2])**2)

    t = (p['RotDet'][0, 0] * (p['Lsam2det'] - SamposW[0]) + p['RotDet'][1, 0] * (p['dety00'] - SamposW[1]) + p['RotDet'][2, 0] * (p['detz00'] - SamposW[2])) / \
        (p['RotDet'][0, 0] * K_out_unit[0] + p['RotDet'][1, 0] * K_out_unit[1] + p['RotDet'][2, 0] * K_out_unit[2])

    dety_mm = np.dot(p['RotDet'][:, 1], t * K_out_unit + np.array([SamposW[0] - p['Lsam2det'], SamposW[1] - p['dety00'], SamposW[2] - p['detz00']]).T)  # dety in mm
    detz_mm = np.dot(p['RotDet'][:, 2], t * K_out_unit + np.array([SamposW[0] - p['Lsam2det'], SamposW[1] - p['dety00'], SamposW[2] - p['detz00']]).T)  # detz in mm

    dety = -dety_mm / p['pixelysize'] + p['dety0']  # dety in pixels
    detz = -detz_mm / p['pixelzsize'] + p['detz0']  # detz in pixels

    if p['flip_lr_flag'] == True:
        dety = p['detysize'] - dety + 2 * (p['dety0'] - p['detysize'] / 2)
        dety_mm = p['detysize'] * p['pixelysize'] - dety_mm

    if p['flip_ud_flag'] == True:
        detz = p['detzsize'] - detz + 2 * (p['detz0'] - p['detzsize'] / 2)
        detz_mm = p['detzsize'] * p['pixelzsize'] - detz_mm

    if (1 <= dety <= p['detysize']) and (1 <= detz <= p['detzsize']) and not \
       (p['BeamStopY'][0] <= dety <= p['BeamStopY'][1] and p['BeamStopZ'][0] <= detz <= p['BeamStopZ'][1]):
        HitDetFlag = True
    else:
        HitDetFlag = False

    return dety, detz, dety_mm, detz_mm, Gt.T, HitDetFlag


def get_omega_matrix(omega):
    """
    Calculate the rotation matrix (Omega) for a rotation angle around Z vertical axis in right-hand coordinate system
    Returns:
    Omega matrix 3*3 array
    """
    omega_matrix = np.array([[np.cos(omega), -np.sin(omega), 0],
                   [np.sin(omega), np.cos(omega), 0],
                   [0, 0, 1.0]])
    return omega_matrix
    

def find_omega(a, b, c, d, sqD):
    """
    Finds the omega (rotation angle) that generates the diffraction.

    Arguments:
    a, b, c, d, sqD -- parameters as per the problem's equation

    Returns:
    Omega -- list containing the solutions for omega (in the range [0, 2*pi])
    """
    Omega = []
    if sqD > 0:
        sqD = np.sqrt(sqD)
        
        # First Omega solution
        comega = (a*c + b*sqD) / d
        somega = (b*c - a*sqD) / d
        Omega.append(np.arccos(comega))
        if somega < 0:
            Omega[0] = -Omega[0]

        # Second Omega solution
        comega = comega - 2.0 * b * sqD / d
        somega = somega + 2.0 * a * sqD / d
        Omega.append(np.arccos(comega))
        if somega < 0:
            Omega[1] = -Omega[1]

        # Bring solutions to the range [0, 2*pi]
        for i in range(2):
            if Omega[i] < 0:
                Omega[i] = 2 * np.pi + Omega[i]
    else:
        Omega = [np.nan]

    return Omega


def get_hkls(ucell, dsmax = 1.2):
    """
    get hkl list from the ucell provided by ImageD11

    Arguments:
    ucell, dsmax

    Returns:
    ds_all -- 1/d of all the unique hkl families within dsmax
    hkls   -- list of hkl indices for the lattice planes
    """
    Ahkls = ucell.gethkls(dsmax)
    unique_dict = {}

    # Populate the dictionary with the unique first-column values as keys and corresponding second-column values as lists
    for val in Ahkls:
        first_col, second_col = val
        if first_col not in unique_dict:
            unique_dict[first_col] = []
        unique_dict[first_col].append(second_col)

    # Convert the lists of second-column values to numpy arrays
    unique_arrays = {k: np.array(v) for k, v in unique_dict.items()}

    # Display the unique values and corresponding numpy arrays
    #for k, v in unique_arrays.items():
        
    ds_all = []
    hkls = []
    for key in unique_arrays:
        ds_all.append(key)
        hkls.append(unique_arrays[key])
    ds_all = np.vstack(ds_all)
    hkls = np.vstack(hkls)
    print('Got {} hkl lattice planes in total out of {} hkl families'.format(hkls.shape[0], ds_all.shape[0]))
    print('ds range for {} hkl families: [{}, {}]'.format(ds_all.shape[0], np.min(ds_all), np.max(ds_all)))

    return ds_all, hkls
                            

def get_tth_from_ds(ds, wavelength):
    """
    Calculate two-theta angle from ds and wavelength according to Bragg's law

    Arguments:
    ds -- inverse of d-spacing for a given hkl family
    wavelength -- [Angstrom]

    Returns:
    two-theta in degrees
    """
    return 2*np.rad2deg(np.arcsin(wavelength*ds/2.0))                  


def get_ds_max(pars):
    """
    Calculate two-theta angle from ds and wavelength according to Bragg's law

    Arguments:
    pars    -- ImageD11 pars object

    Returns:
    ds_max  -- maximum ds spacing detectable on the detector [Anstrom^-1]
    """    
    
    p = pars_conversion.convert_ImageD11pars2p(pars)
    ttheta_max = np.arctan(  ((p['detysize']*p['pixelysize']/2)**2 + (p['detzsize']*p['pixelzsize']/2)**2)**(1/2) / p['Lsam2det']  )   # two-teta maximum [radian]
    theta_max = ttheta_max/2.0   # theta [radian]
    wavelength = econst / p['Energy']
    ds_max = 1.0/(p['wavelength']/(2*np.sin(theta_max)))  # 1/d [Angstron^-1]    
    print('Maximum two-theta angle = {}, ds_max = {} Angstrom^-1'.format(np.rad2deg(ttheta_max), ds_max))
    
    return ds_max


def find_matching_peaks(cf, fwd, dsmax=1.5, tol_angle=0.1, tol_pixel=0.55):
    """
    Matching the peaks from cf to the forward calculated peaks by checking differences in omega, tth, fc(dety), sc(detz)

    Arguments:
    cf -- ImageD11 column file [object]
    fwd -- forward calculated peaks info obtained from forward_comp [list]
    dsmax -- maximum ds [Angstrom^-1]
    tol_angle -- tolerance for matching angles, omega and two-theta [deg]
    tol_pixel -- tolerance for matching pixel coordinate, dety (fc) and detz (sc), [pixel]

    Returns:
    cf_matched -- colume file that only matches with forward calculated peaks
    fwd_matched -- matched peaks among all the forward calculated peaks
    ij -- indices corresponding to cf_matched and fwd_matched, respectively
    Completeness -- the fraction of matched forward peaks
    """
    
    cf_omega = np.array(cf.omega)
    cf_tth = np.array(cf.tth)
    cf_fc = np.array(cf.fc)
    cf_sc = np.array(cf.sc)
    
    # Extract forward peaks into arrays for vectorized matching
    fwd_omega = np.array([row[0] for row in fwd])
    fwd_tth = np.array([row[2] for row in fwd])
    fwd_fc = np.array([row[4] for row in fwd])
    fwd_sc = np.array([row[5] for row in fwd])
    
    # Vectorized differences
    diff_omega = np.abs(cf_omega[:, np.newaxis] - fwd_omega)
    diff_tth = np.abs(cf_tth[:, np.newaxis] - fwd_tth)
    diff_fc = np.abs(cf_fc[:, np.newaxis] - fwd_fc)
    diff_sc = np.abs(cf_sc[:, np.newaxis] - fwd_sc)
    
    # Apply tolerances
    matches = (diff_omega < tol_angle) & (diff_tth < tol_angle) & (diff_fc < tol_pixel) & (diff_sc < tol_pixel)
    
    
    # Create a boolean mask for the cf object
    matched_cf_indices, matched_fwd_indices = np.where(matches)
    
    matched_mask = np.zeros(cf.nrows, dtype=bool)
    matched_mask[matched_cf_indices] = True
    
    # Filter cf_matched using the boolean mask
    cf_matched = cf.copy()
    cf_matched.filter(matched_mask)
    
    # Get the corresponding forward matched rows
    fwd_matched = [fwd[j] for j in np.unique(matched_fwd_indices)]
    
    # ij will contain the indices of matched peaks
    ij = list(zip(matched_cf_indices, matched_fwd_indices))
    
    # Calculate the completeness
    Completeness = len(fwd_matched) / len(fwd)
    print('Found {}/{} matched peaks, completeness = {}'.format(len(fwd_matched), len(fwd), Completeness))
    
    return cf_matched, fwd_matched, ij, Completeness



def cf_remove_weak_peaks(cf, percent_int = 20, thres_int = None):
    """
    remove weak peaks either by specifying the minimum peak intensity or by removing the lowest percent_int percent of the all peaks
    Matching the peaks from cf to the forward calculated peaks by checking differences in omega, tth, fc(dety), sc(detz)

    Arguments:
    cf -- ImageD11 column file [object]
    percent_int -- percentage of the weak intensities
    thres_int -- threshold intenisty

    Returns:
    filterd peaks as cf object
    """
    if thres_int is None:
        thres_int = np.percentile(cf.sum_intensity, percent_int)
    print('Intensity threshold: {}'.format(thres_int))
    m = cf.sum_intensity > thres_int
    cf_out = cf.copy()
    cf_out.filter(m)
    
    print('Original {} peaks reduced to {} peaks'.format(cf.nrows, cf_out.nrows))
    return cf_out


def cf_set_difference(cf1, cf2, tol = 0.001):
    """
    Matching the peaks from cf to the forward calculated peaks by checking differences in omega, tth, fc(dety), sc(detz)

    Arguments:
    cf1 -- ImageD11 column file [object]
    cf2 -- ImageD11 column file [object]
    tol -- tolerance for matching the peaks, dty, omega, sc and fc

    Returns:
    cf1_diff -- rest of the peaks from cf1 which is not contained in cf2
    cf2_diff -- rest of the peaks from cf2 which is not contained in cf1
    """
    
    cf1_omega = np.array(cf1.omega)
    cf1_dty = np.array(cf1.dty)
    cf1_fc = np.array(cf1.fc)
    cf1_sc = np.array(cf1.sc)
    
    cf2_omega = np.array(cf2.omega)
    cf2_dty = np.array(cf2.dty)
    cf2_fc = np.array(cf2.fc)
    cf2_sc = np.array(cf2.sc)
    
    # Vectorized differences
    diff_dty = np.abs(cf1_dty[:, np.newaxis] - cf2_dty)
    diff_omega = np.abs(cf1_omega[:, np.newaxis] - cf2_omega)
    diff_fc = np.abs(cf1_fc[:, np.newaxis] - cf2_fc)
    diff_sc = np.abs(cf1_sc[:, np.newaxis] - cf2_sc)
    
    # Apply tolerances
    matches = (diff_dty < tol) & (diff_omega < tol) & (diff_fc < tol) & (diff_sc < tol)
    
    # Create a boolean mask for the cf object
    matched_cf1_indices, matched_cf2_indices = np.where(matches)
    
    diff_mask1 = np.ones(cf1.nrows, dtype=bool)
    diff_mask1[matched_cf1_indices] = False
    
    diff_mask2 = np.ones(cf2.nrows, dtype=bool)
    diff_mask2[matched_cf2_indices] = False
    
    # Filter cf using the boolean mask
    cf1_diff = cf1.copy()
    cf1_diff.filter(diff_mask1)
    
    cf2_diff = cf2.copy()
    cf2_diff.filter(diff_mask2)
    
    print('Found {}/{} different peaks for cf1'.format(cf1_diff.nrows, cf1.nrows))
    print('Found {}/{} different peaks for cf2'.format(cf2_diff.nrows, cf2.nrows))

    return cf1_diff, cf2_diff


def cf_plot_sino(cfs):
    """
    plot sinogram for each cf wrapped in a list
    """
    if isinstance(cfs, list):
        ncf = len(cfs)  # assume the input is a list containing multiple cf
    else:
        cfs = [cfs]     # wrap the single cf in a list
        ncf = 1         # assume the input is a single cf
    print('Got {} colume file object(s)'.format(ncf))
        
    f, a = plt.subplots(1, ncf, figsize=(6*ncf, 6))
    
    # If there is only one subplot (ncf == 1), treat `a` as a list for consistency
    if ncf == 1:
        a = [a]
    
    for i, cf in enumerate(cfs):
        scatter = a[i].scatter(cf.omega, cf.dty, 
                       c=np.log10(cf.sum_intensity), s=8, cmap='viridis')
        cbar = plt.colorbar(scatter, ax=a[i])
        cbar.set_label('Sum Intensity')  # Label for the colorbar
        a[i].set_xlabel('Omega ($^{o}$)')
        a[i].set_ylabel('dty ($\mu$m)')
    
    plt.tight_layout()
    plt.show()
    
    
def cf_remove_unmatched_peaks(cf_strong, cf_matched_all):
    """
    Remove unmatched peaks from the cf_strong so that grain sinograms are expected to be cleaner after re-assigning the cleaned cf_strong

    Arguments:
    cf_strong -- ImageD11 colume file, e.g. cf_4d = ds.get_cf_4d_from_disk() and then filter
    cf_matched_all -- list of matched peaks for each of the grain, list of ImageD11.columnfile

    Returns:
    cf_clean -- cleaned cf_strong
    """
    cf_diff = cf_strong.copy()
    for cf_matched in cf_matched_all:
        print('cf_diff has {} peaks'.format(cf_diff.nrows))
        cf_diff, _ = cf_set_difference(cf_diff, cf_matched, tol = 0.001)
    
    cf_clean, _ = cf_set_difference(cf_strong, cf_diff, tol = 0.001)
    
    return cf_clean
    

def cf_filter_for_grain(cf, grain_id = 0):
    m = cf.grain_id == grain_id
    cf_out = cf.copy()
    cf_out.filter(m)   
    print('Original {} peaks filtered to {} peaks'.format(cf.nrows, cf_out.nrows))
    return cf_out