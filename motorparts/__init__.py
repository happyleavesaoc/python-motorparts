"""Mopar API."""

import os
import logging
import json
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
REMOTE_COMMAND_URL = 'https://www.mopar.com/moparsvc/remoteCommands'
REMOTE_STATUS_URL = 'https://www.mopar.com/moparsvc/vehicle/remote/status'
COOKIE_PATH = './motorparts_cookies.pickle'
ATTRIBUTION = 'Information provided by www.mopar.com'
USER_AGENT = 'Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 ' \
             '(KHTML, like Gecko) Chrome/57.0.2987.133 Safari/537.36'

COMMAND_LOCK = 'lock'
COMMAND_UNLOCK = 'unlock'
COMMAND_ENGINE_ON = 'engineon'
COMMAND_ENGINE_OFF = 'engineoff'
COMMAND_HORN = 'horn'
SUPPORTED_COMMANDS = [COMMAND_LOCK, COMMAND_UNLOCK, COMMAND_ENGINE_ON,
                      COMMAND_ENGINE_OFF, COMMAND_HORN]


class MoparError(Exception):
    """Mopar error."""

    pass


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
    except json.decoder.JSONDecodeError:
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
                'model': vehicle['model'].split(' ')[0],
                'odometer': vehicle['odometerMileage']
            } for vehicle in profile['vehicles']
        ]
    }
def _remote_status(session, service_id, vin):
    """Poll for remote command status."""
    _LOGGER.info('polling for status')
    resp = session.post(REMOTE_STATUS_URL, {
        'serviceID': service_id,
        'vin': vin
    }).json()
    if not resp['active']:
        return 'failed'
    elif resp['status'] == 'Successful':
        return 'completed'
    time.sleep(3)
    return _remote_status(session, service_id, vin)


@token
def remote_command(session, command, vehicle_index, poll=False):
    """Send a remote command."""
    if command not in SUPPORTED_COMMANDS:
        raise MoparError("unsupported command: " + command)
    profile = get_profile(session)
    _validate_vehicle(vehicle_index, profile)
    resp = session.post(REMOTE_COMMAND_URL, {
        'pin': session.auth.pin,
        'uuid': profile['vehicles'][vehicle_index]['uuid'],
        'action': command
    }).json()
    if poll:
        return _remote_status(session, resp['customerServiceId'],
                              resp['vehicleId'])


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
