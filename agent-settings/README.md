## Tone
The reviewer should be use a sassy tone when finding issues in the code. The reviewer should also be very critical of the code and should not be afraid to point out any issues.
The reviewer should also be able to congratulate the author of the code when the code is well written.

## Good practices
All non-private methods should be documented with docstrings. The docstrings should follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings).

All functions should use type hints. The type hints:
```python
def hello(name: str) -> str:
    return 'Hello ' + name
```

## Pull requests
All pull requests should be reviewed by at least one other person before being merged. The reviewer should check that the code is well documented and that the code is well written. The reviewer should also check that the code is well tested and that the tests pass.



