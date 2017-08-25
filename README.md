# python-motorparts

Access data and functionality of vehicles on your [mopar.com](http://mopar.com) account. Requires active **uConnect** subscription.

- Lock/unlock
- Engine on/off
- Horn/lights
- Vehicle health report
- Mileage
- Other basic information

## Usage
```python
import motorparts
session = motorparts.get_session('username', 'password', 5555)  # pin
summary = motorparts.get_summary(session)
report = motorparts.get_report(session, 0)  # vehicle index
tow_guide = motorparts.get_tow_guide(session, 0)  # vehicle index
motorparts.lock(session, 0, true)  # vehicle index, poll for ack
```
## Caching

Session cookies are cached by default in `./motorparts_cookies.pickle` and will be used if available instead of logging in. If the cookies expire, a new session will be established automatically.

## Development

### Lint

`tox`

### Release

`make release`

### Contributions

Contributions are welcome. Please submit a PR that passes `tox`.

### TODO

- [ ] Get vehicle location coordinates (only available in mobile app)
- [ ] Async command acknowledgement

## Disclaimer
Not affiliated with FCA US LLC. Use at your own risk.
