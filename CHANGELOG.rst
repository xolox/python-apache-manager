Changelog
=========

The purpose of this document is to list all of the notable changes to this
project. The format was inspired by `Keep a Changelog`_. This project adheres
to `semantic versioning`_.

.. contents::
   :local:

.. _Keep a Changelog: http://keepachangelog.com/
.. _semantic versioning: http://semver.org/

`Release 1.1`_ (2017-03-06)
---------------------------

- Added hanging worker detection based on hard coded five minute threshold (to
  be made configurable in a future release).

- Made ``test_refresh()`` compatible with Ubuntu 16.04:

  I've just upgraded my personal and work laptops to Ubuntu 16.04 and noticed
  that several tests have started failing. Most noticeably the server uptime
  reported on the status page is no longer updated consistently. I'm changing
  this test to check a different status page item which should be more
  reliable.

- Made worker kill tests compatible with Ubuntu 16.04 (Apache 2.4).

.. _Release 1.1: https://github.com/xolox/python-apache-manager/compare/1.0...1.1

`Release 1.0`_ (2017-02-15)
---------------------------

- Refactor ``WorkerStatus`` class to properly use property-manager_.

  Strictly speaking this change set breaks backwards compatibility, however 99%
  percent of the functionality is the same, I've mostly just changed a whole
  lot of undocumented implementation details. Nevertheless I'm bumping the
  major version number because "explicit is better than implicit".

- Use six_ instead of homegrown ``apache_manager.compat`` module.

  Six_ was already included in the transitive requirements via executor_ so
  there was really no point in not using it 🙂.

- Refactor makefile & setup script (checkers, docs, wheels, twine, etc).

- Improve test coverage of port discovery

.. _Release 1.0: https://github.com/xolox/python-apache-manager/compare/0.6...1.0
.. _six: https://pypi.org/project/six/
.. _executor: https://pypi.org/project/executor/

`Release 0.6`_ (2016-05-27)
---------------------------

Make it easy to silence apache-manager in cron jobs.

.. _Release 0.6: https://github.com/xolox/python-apache-manager/compare/0.5...0.6

`Release 0.5`_ (2016-05-27)
---------------------------

Enable Zabbix low level discovery of WSGI process groups.

.. _Release 0.5: https://github.com/xolox/python-apache-manager/compare/0.4...0.5

`Release 0.4`_ (2016-05-27)
---------------------------

Expose Apache worker memory usage in data file.

.. _Release 0.4: https://github.com/xolox/python-apache-manager/compare/0.3...0.4

`Release 0.3`_ (2016-05-27)
---------------------------

Properly parse ``Listen`` directives in ``/etc/apache2/ports.conf`` (not so
much a bug fix but definitely a quality boost).

.. _Release 0.3: https://github.com/xolox/python-apache-manager/compare/0.2...0.3

`Release 0.2`_ (2015-10-04)
---------------------------

Internal refactoring: Switch from cached-property_ to property-manager_.

.. _Release 0.2: https://github.com/xolox/python-apache-manager/compare/0.1.1...0.2
.. _cached-property: https://pypi.org/project/cached-property/
.. _property-manager: https://pypi.org/project/property-manager/

`Release 0.1.1`_ (2015-09-27)
-----------------------------

Bug fix: Explicitly specify BeautifulSoup parser.

This avoids BeautifulSoup from emitting the following warning:

 "No parser was explicitly specified, so I'm using the best available HTML
 parser for this system ("html.parser"). This usually isn't a problem, but if
 you run this code on another system, or in a different virtual environment, it
 may use a different parser and behave differently."

About the choice for ``html.parser``: This is the only parser built into the
Python standard library and it seems to work fine for Apache status pages
(which makes sense because these pages don't contain arbitrary invalid HTML,
they are fairly well formed and simple) so I don't see any point in pulling in
another external dependency.

.. _Release 0.1.1: https://github.com/xolox/python-apache-manager/compare/0.1...0.1.1

`Release 0.1`_ (2015-09-26)
---------------------------

Initial commit and release based on several years of experience monitoring
Apache web servers at large.

.. _Release 0.1: https://github.com/xolox/python-apache-manager/tree/0.1
