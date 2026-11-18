"""Explicit public IPv4 discovery; no project content or credentials are sent."""
import ipaddress
import json
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

PUBLIC_IP_URL = 'https://api.ipify.org?format=json'
PUBLIC_IP_SERVICES = (PUBLIC_IP_URL, 'https://checkip.amazonaws.com/', 'https://ipv4.icanhazip.com/')
LOOKUP_TIMEOUT = 4


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('The public IP service redirected unexpectedly.')


def _public_ipv4(raw):
    if len(raw) > 1024:
        raise ValueError('Public IP service response was too large.')
    text = raw.decode('utf-8').strip()
    if text.startswith('{'):
        data = json.loads(text)
        if not isinstance(data, dict) or not isinstance(data.get('ip'), str):
            raise ValueError('Public IP service returned an invalid response.')
        text = data['ip'].strip()
    address = ipaddress.IPv4Address(text)
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise ValueError('The service did not return a public IPv4 address.')
    return str(address)


def detect_public_ipv4():
    failures = []
    opener = build_opener(NoRedirect())
    for url in PUBLIC_IP_SERVICES:
        request = Request(url, headers={'Accept': 'application/json, text/plain', 'User-Agent': 'PocketEditor-TeamProject'})
        try:
            with opener.open(request, timeout=LOOKUP_TIMEOUT) as response:
                return _public_ipv4(response.read(1025))
        except HTTPError as error:
            reason = 'HTTP ' + str(error.code)
        except (URLError, OSError) as error:
            cause = error.reason if isinstance(error, URLError) else error
            if isinstance(cause, socket.gaierror):
                reason = 'DNS lookup failed'
            elif isinstance(cause, ssl.SSLCertVerificationError):
                reason = 'TLS certificate verification failed'
            elif isinstance(cause, TimeoutError):
                reason = 'request timed out'
            else:
                reason = 'connection failed'
        except (ValueError, UnicodeError):
            reason = 'invalid public IPv4 response'
        failures.append(url.split('/')[2] + ': ' + reason)
    raise RuntimeError('Public IP lookup failed. ' + '; '.join(failures) +
                       '. Check internet access, DNS, or proxy settings and retry.')


def public_https_url(address, port='443'):
    address = ipaddress.IPv4Address(address)
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise ValueError('Detect a valid public IPv4 address first.')
    try:
        number = int(port)
    except (TypeError, ValueError):
        raise ValueError('Enter an HTTPS port from 1 to 65535.') from None
    if not 1 <= number <= 65535:
        raise ValueError('Enter an HTTPS port from 1 to 65535.')
    return f'https://{address}' + (f':{number}' if number != 443 else '')


def suggested_lan_ipv4():
    """Ask the OS for a default-route source address without sending a datagram."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(.5)
            probe.connect(('192.0.2.1', 9))
            address = ipaddress.IPv4Address(probe.getsockname()[0])
            if not address.is_loopback and not address.is_unspecified and not address.is_multicast:
                return str(address)
    except OSError:
        pass
    return None


def lan_host_url(bind, port, suggested=None):
    number = int(port)
    if not 0 < number <= 65535:
        return None
    address = suggested if bind == '0.0.0.0' else bind
    if not address:
        return None
    parsed = ipaddress.IPv4Address(address)
    if parsed.is_unspecified or parsed.is_multicast or parsed.is_global:
        return None  # Public addresses need a separately configured HTTPS endpoint.
    return f'http://{parsed}:{number}'
