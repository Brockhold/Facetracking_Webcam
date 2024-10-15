I've set this up on a few different devices. Usually my target is a low power linux platform running some distribution downstream from debian or arch.
It's usually as simple as:
* clone repo
* create python venv
* inside venv, install from requirements
* run main.py

But sometimes there are issues. For example, being unable to install a package from pip, usually a result of a build not working.