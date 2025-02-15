'''
This module shall provide capability to :
1) <CREATE MEASURED BR TIMESERIES> Given input spacecraft and timestamp, compute 1 hour 
most probable polarity for 1 carrington rotation centered on timestamp
2) <CREATE PREDICTED BR TIMESERIES> Given model NL, input spacecraft and timestamp, compute predicted timeseries of polarity  
3) <DO NL SCORE> Read in measured and predicted Br, compute NL Score
'''
import astropy.units as u
import datetime
import numpy as np
import cmfpy.utils as utils
import cmfpy.projection as projection
import cmfpy.io as io
import os

from scipy.interpolate import interp1d
import sunpy.coordinates
import sunpy.map

def determine_carrington_interval(center_date,body) :
    inst_body_position = projection.create_carrington_trajectory(
        [center_date],body
        )
    inst_lon = inst_body_position.lon
    inst_antipode = (inst_body_position.lon.value + 180 % 360) *u.deg
    two_month_window = utils.gen_dt_arr(
        center_date-datetime.timedelta(days=30),
        center_date+datetime.timedelta(days=30),
        cadence_days=6/24
        )
    two_month_trajectory = projection.create_carrington_trajectory(
        two_month_window,body
        )
    carr_inds = np.where(np.diff(
        (two_month_trajectory.lon-inst_antipode).value % 360
        ) > 180)[0] + 1

    carrington_interval = two_month_window[carr_inds]

    return carrington_interval

def make_hourly_medians(datetimes,data) :
    timestamps = np.array([t.timestamp() for t in datetimes])  
    datetime_hourly = utils.gen_dt_arr(
        datetimes[0],
        datetimes[-1],
        cadence_days=1/24
        )  
    ts_edges = np.array([t.timestamp() for t in datetime_hourly[1:-1]])
    
    argsplit = [np.where(timestamps > te)[0][0] for te in ts_edges]
    data_split = np.split(data,argsplit)
    medians = np.array([np.nanmedian(slice_) for slice_ in data_split])
    return  (datetime_hourly[:-1]+datetime.timedelta(hours=0.5),
             medians)


def create_polarity_obs(center_date,body,return_br,
                        save_dir=os.path.join(f"{__path__[0]}","data")
                        ):
    '''
    Given `center_date`:`datetime.datetime` and `spacecraft`*:`str`,
    1) determine the time interval required to span a Carrington 
    rotation worth of data, producing t_start, t_end
    2) download measured magnetic field data from the relevant
     spacecraft* between those dates
    3) For every hour, compute a histogram of measurements and find the
    most probable measurement, take its sign to obtain polarity
    4) Return timeseries and save in save_dir.

    * should validate spacecraft spice kernels are accessible via
    astrospice
    '''
    carrington_interval = determine_carrington_interval(
        center_date,body
        )
    ### Change pyspedas directory to nlmetric/data

    data = io.download_br_data(carrington_interval, body)

    if body == "L1" : 
        times_medians,br_medians = make_hourly_medians(
            data[list(data.keys())[0]]['x'],
            data[list(data.keys())[0]]['y'],
        )

        br_medians *= -1 # Convert GSE-X to RTN-R
    
    else :
        times_medians,br_medians = make_hourly_medians(
            data[list(data.keys())[0]]['x'],
            data[list(data.keys())[0]]['y'][:,0],
        )
    

    ## Interpolate to 1 hour edges inside carrington interval
    datetimes_hourly=utils.gen_dt_arr(*carrington_interval,cadence_days=1/24)
    br_hourly = interp1d(utils.datetime2unix(times_medians),
                         br_medians,
                         bounds_error=False)(utils.datetime2unix(datetimes_hourly))

    if return_br :  return datetimes_hourly, br_hourly # Return br in nT
    else : return  datetimes_hourly,np.sign(br_hourly) # or return sign(br)

def create_polarity_model(model_NL_map, center_date, body, 
                          constant_vr=False, altitude=2.5*u.R_sun,save_dir=os.path.join(f"{__path__[0]}","data"),
                          return_vr=False,
                          ):
    '''
    Given `model_NLmap` (modeled neutral line map user provided), 
    `center_date`:`datetime.datetime` (which should match the 
    magnetogram date of the neutral line map) and an astrospice-valid
    `spacecraft`:`str`: 
    1) Determine the time interval required to span a Carrington
    rotation worth of data, producing `t_start`, `t_end`
    2) construct a ballistically mapped trajectory of the spacecraft 
    at the altitude of the model NL map (default 2.5Rs) between those
    two dates. 
    3) Fly spacecraft over the NL map and sample the predicted magnetic
    polarity as a function of time
    4) Return predicted timeseries and save in save_dir
    '''

    carrington_interval = determine_carrington_interval(center_date,body)

    datetimes_hourly = utils.gen_dt_arr(*carrington_interval, cadence_days=1/24)

    if constant_vr == True: vr_arr = None
    else:
        data = io.download_vr_data(carrington_interval, body)

        if body == "L1" :
            times_medians,vr_medians = make_hourly_medians(
                data[list(data.keys())[0]]['x'],
                data[list(data.keys())[0]]['y'],
            )

            vr_medians *= -1
        elif (body == 'stereo-a') or (body == 'stereo-b'):
            times_medians,vr_medians = make_hourly_medians(
                data[list(data.keys())[0]]['x'],
                data[list(data.keys())[0]]['y'],
            )
        else:
            print(data)
            times_medians,vr_medians = make_hourly_medians(
                data[list(data.keys())[0]]['x'],
                data[list(data.keys())[0]]['y'][:,0],
            )

        nan_filt = np.where(~np.isnan(vr_medians))
        vr_medians = vr_medians[nan_filt]
        times_medians = times_medians[nan_filt]

        vr_arr = interp1d(utils.datetime2unix(times_medians),
                         vr_medians,
                         bounds_error=False,fill_value=360)(utils.datetime2unix(datetimes_hourly))*u.km/u.s
    
    carrington_trajectory = projection.create_carrington_trajectory(
        datetimes_hourly,body,obstime_ref=center_date
        )
    
    projected_trajectory = projection.ballistically_project(carrington_trajectory,
                                                   r_inner=altitude, vr_arr=vr_arr)
    
    polarity_modeled = sunpy.map.sample_at_coords(model_NL_map, 
                                                  projected_trajectory)
    if return_vr: return (datetimes_hourly, polarity_modeled) , vr_arr
    else: return datetimes_hourly, polarity_modeled
    
def compute_NL_metric(model_tseries,obs_tseries) :
    '''
    Given `model_tseries` and `obs_tseries`, ensure the timestamps are
    aligned, compute the dot product of the data, and divide by the
    number of the datapoints to obtain the NL_metric score.
    '''
    model_tstamps = utils.datetime2unix(model_tseries[0])
    model_pol = np.sign(model_tseries[1])
    obs_tstamps = utils.datetime2unix(obs_tseries[0])
    obs_pol = np.sign(obs_tseries[1])

    if ((len(model_tstamps) != len(obs_tstamps)) |
        (model_tstamps[0] != obs_tstamps[0]) |
        (model_tstamps[-1] != obs_tstamps[-1])
    ) : 
        obs_pol = interp1d(obs_tstamps,obs_pol,bounds_error=False)(model_tstamps)

    mult = obs_pol*model_pol
    return np.nansum(mult[mult == 1])/len(~np.isnan(mult))