"""Mopar API."""

import os
import logging
# pylint: disable=wrong-import-position
import json
try:
    from json.decoder import JSONDecodeError
except ImportError:
    JSONDecodeError = ValueError
import pickle
import time
import requests
from requests.auth import AuthBase
from bs4 import BeautifulSoup


_LOGGER = logging.getLogger(__name__)
HTML_PARSER = 'html.parser'
SIGNIN_URL = 'https://www.mopar.com/sign-in'
SSO_URL = 'https://sso.extra.chrysler.com/siteminderagent/forms/b2clogin.fcc'
TARGET_URL = 'https://sso.extra.chrysler.com/cgi-bin/moparproderedirect.cgi?' \
             'env=prd&PartnerSpId=B2CAEM&IdpAdapterId=B2CSM&appID=MOPUSEN_C&' \
             'TargetResource=' + SIGNIN_URL
PROFILE_URL = 'https://www.mopar.com/moparsvc/user/getProfile'
TOKEN_URL = 'https://www.mopar.com/moparsvc/token'
TOW_URL = 'https://www.mopar.com/moparsvc/vehicle/tow-guide/vin'
VHR_URL = 'https://www.mopar.com/moparsvc/getVHR'
REMOTE_LOCK_COMMAND_URL = 'https://www.mopar.com/moparsvc/connect/lock'
REMOTE_ENGINE_COMMAND_URL = 'https://www.mopar.com/moparsvc/connect/engine'
REMOTE_ALARM_COMMAND_URL = 'https://www.mopar.com/moparsvc/connect/alarm'
COOKIE_PATH = './motorparts_cookies.pickle'
ATTRIBUTION = 'Information provided by www.mopar.com'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' \
             '(KHTML, like Gecko) Chrome/64.0.3282.140 Safari/537.36 Edge/18.17763'
COMMAND_LOCK = 'LOCK'
COMMAND_UNLOCK = 'UNLOCK'
COMMAND_ENGINE_ON = 'START'
COMMAND_ENGINE_OFF = 'STOP'
COMMAND_HORN = 'HORN_LIGHT'
SUPPORTED_COMMANDS = [COMMAND_LOCK, COMMAND_UNLOCK, COMMAND_ENGINE_ON,
                      COMMAND_ENGINE_OFF, COMMAND_HORN]


class MoparError(Exception):
    """Mopar error."""


def _save_cookies(requests_cookiejar, filename):
    """Save cookies to a file."""
    with open(filename, 'wb') as handle:
        pickle.dump(requests_cookiejar, handle)


def _load_cookies(filename):
    """Load cookies from a file."""
    with open(filename, 'rb') as handle:
        return pickle.load(handle)


def _login(session):
    """Login."""
    _LOGGER.info("logging in (no valid cookie found)")
    session.cookies.clear()
    resp = session.post(SSO_URL, {
        'USER': session.auth.username,
        'PASSWORD': session.auth.password,
        'TARGET': TARGET_URL
    })
    parsed = BeautifulSoup(resp.text, HTML_PARSER)
    relay_state = parsed.find('input', {'name': 'RelayState'}).get('value')
    saml_response = parsed.find('input', {'name': 'SAMLResponse'}).get('value')
    session.post(SIGNIN_URL, {
        'RelayState': relay_state,
        'SAMLResponse': saml_response
    })
    session.get(SIGNIN_URL)
    _save_cookies(session.cookies, session.auth.cookie_path)


def _validate_vehicle(vehicle_index, profile):
    """Validate vehicle index."""
    if vehicle_index >= len(profile['vehicles']) or vehicle_index < 0:
        raise MoparError("vehicle does not exist")


def authenticated(function):
    """Re-authenticate if session expired."""
    def wrapped(*args):
        """Wrap function."""
        try:
            return function(*args)
        except MoparError:
            _LOGGER.info("attempted to access page before login")
            _login(args[0])
            return function(*args)
    return wrapped


def token(function):
    """Attach a CSRF token for POST requests."""
    def wrapped(session, *args):
        """Wrap function."""
        resp = session.get(TOKEN_URL).json()
        session.headers.update({'mopar-csrf-salt': resp['token']})
        return function(session, *args)
    return wrapped


@authenticated
def get_profile(session):
    """Get complete profile."""
    try:
        profile = session.get(PROFILE_URL).json()
        if 'errorCode' in profile and profile['errorCode'] == '403':
            raise MoparError("not logged in")
        return profile
    except JSONDecodeError:
        raise MoparError("not logged in")


def get_report(session, vehicle_index):
    """Get vehicle health report summary."""
    vhr = get_vehicle_health_report(session, vehicle_index)
    if 'reportCard' not in vhr:
        raise MoparError("no vhr found")
    return _traverse_report(vhr['reportCard'])


@authenticated
def get_vehicle_health_report(session, vehicle_index):
    """Get complete vehicle health report."""
    profile = get_profile(session)
    _validate_vehicle(vehicle_index, profile)
    return session.get(VHR_URL, params={
        'uuid': profile['vehicles'][vehicle_index]['uuid']
    }).json()


def _traverse_report(data):
    """Recursively traverse vehicle health report."""
    if 'items' not in data:
        return {}
    out = {}
    for item in data['items']:
        skip = (item['severity'] == 'NonDisplay' or
                item['itemKey'] == 'categoryDesc' or
                item['value'] in [None, 'Null', 'N/A', 'NULL'])
        if skip:
            continue
        value = 'Ok' if item['value'] == '0.0' else item['value']
        out[item['itemKey']] = value
        out.update(_traverse_report(item))
    return out


@authenticated
@token
def get_tow_guide(session, vehicle_index):
    """Get tow guide information."""
    profile = get_profile(session)
    _validate_vehicle(vehicle_index, profile)
    return session.post(TOW_URL, {
        'vin': profile['vehicles'][vehicle_index]['vin']
    }).json()


def _get_model(vehicle):
    """Clean the model field. Best guess."""
    model = vehicle['model']
    model = model.replace(vehicle['year'], '')
    model = model.replace(vehicle['make'], '')
    return model.strip().split(' ')[0]


def get_summary(session):
    """Get vehicle summary."""
    profile = get_profile(session)
    return {
        'user': {
            'email': profile['userProfile']['eMail'],
            'name': '{} {}'.format(profile['userProfile']['firstName'],
                                   profile['userProfile']['lastName'])
        },
        'vehicles': [
            {
                'vin': vehicle['vin'],
                'year': vehicle['year'],
                'make': vehicle['make'],
                'model': _get_model(vehicle),
                'odometer': vehicle['odometerMileage']
            } for vehicle in profile['vehicles']
        ]
    }


def _remote_status(session, serviceRequestId, vin, url, interval=3):
    """Poll for remote command status."""
    _LOGGER.info('polling for status')
    resp = session.get(url, params={
        'remoteServiceRequestID':serviceRequestId,
        'vin':vin
    }).json()
    if resp['status'] == 'SUCCESS':
        return 'completed'
    time.sleep(interval)
    return _remote_status(session, serviceRequestId, vin, url)


@token
def remote_command(session, command, vehicle_index, poll=True):
    """Send a remote command."""
    if command not in SUPPORTED_COMMANDS:
        raise MoparError("unsupported command: " + command)
    profile = get_profile(session)
    _validate_vehicle(vehicle_index, profile)
    if command in [COMMAND_LOCK, COMMAND_UNLOCK]:
        url = REMOTE_LOCK_COMMAND_URL
    elif command in [COMMAND_ENGINE_ON, COMMAND_ENGINE_OFF]:
        url = REMOTE_ENGINE_COMMAND_URL
    elif command == COMMAND_HORN:
        url = REMOTE_ALARM_COMMAND_URL
    resp = session.post(url, {
        'pin': session.auth.pin,
        'vin': profile['vehicles'][vehicle_index]['vin'],
        'action': command
    }).json()
    if poll:
        vin = profile['vehicles'][vehicle_index]['vin']
        serviceRequestId = resp['serviceRequestId']
        return _remote_status(session, serviceRequestId, vin, url)
    return 'submitted'


def lock(session, vehicle_index):
    """Lock."""
    remote_command(session, COMMAND_LOCK, vehicle_index)


def unlock(session, vehicle_index):
    """Unlock."""
    remote_command(session, COMMAND_UNLOCK, vehicle_index)


def engine_on(session, vehicle_index):
    """Turn on the engine."""
    remote_command(session, COMMAND_ENGINE_ON, vehicle_index)


def engine_off(session, vehicle_index):
    """Turn off the engine."""
    remote_command(session, COMMAND_ENGINE_OFF, vehicle_index)


def horn(session, vehicle_index):
    """Horn and lights."""
    remote_command(session, COMMAND_HORN, vehicle_index)


def get_session(username, password, pin, cookie_path=COOKIE_PATH):
    """Get a new session."""
    class MoparAuth(AuthBase):  # pylint: disable=too-few-public-methods
        """Authentication wrapper."""

        def __init__(self, username, password, pin, cookie_path):
            """Init."""
            self.username = username
            self.password = password
            self.pin = pin
            self.cookie_path = cookie_path

        def __call__(self, r):
            """No-op."""
            return r

    session = requests.session()
    session.auth = MoparAuth(username, password, pin, cookie_path)
    session.headers.update({'User-Agent': USER_AGENT})
    if os.path.exists(cookie_path):
        _LOGGER.info("cookie found at: %s", cookie_path)
        session.cookies = _load_cookies(cookie_path)
    else:
        _login(session)
    return session
