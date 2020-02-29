Changelog
=========

The purpose of this document is to list all of the notable changes to this
project. The format was inspired by `Keep a Changelog`_. This project adheres
to `semantic versioning`_.

.. contents::
   :local:

.. _Keep a Changelog: http://keepachangelog.com/
.. _semantic versioning: http://semver.org/

`Release 2.2`_ (2020-02-29)
---------------------------

- Make the command line interface compatible with the ``python -m
  apache_manager …`` command.

- Added :ref:`Zabbix integration for apache-manager` to the online
  documentation.

.. _Release 2.2: https://github.com/xolox/python-apache-manager/compare/2.1...2.2

`Release 2.1`_ (2020-02-27)
---------------------------

This release changes how logging is done:

- When workers are killed this is now logged at the custom level ``NOTICE``
  where previously the level ``INFO`` was used (``NOTICE`` sits between
  ``INFO`` and ``WARNING``). The custom log level is implemented by
  :pypi:`verboselogs`.

- System logging has been reduced so that only killed workers, warnings and
  errors are logged. This is because when ``apache-manager`` is being run from
  a high frequency cron job it shouldn't spam the system logs.

Additionally some changes were made to the test suite:

- Use proper skipping so that :pypi:`pytest` is aware of skipped tests.

- Use :pypi:`pytest-rerunfailures` to automate high level retrying of failed
  tests (duct taping away flaky tests). I'd love to make the test suite more
  robust in the near future but lack the time to do so now.

.. _Release 2.1: https://github.com/xolox/python-apache-manager/compare/2.0...2.1

`Release 2.0`_ (2020-02-26)
---------------------------

**Backwards incompatible changes:**

- Drop support for Python 2.6 and 3.4, start testing on Python 3.8.

- Explicit command line options are now required to enable metrics collection
  and killing of workers. This was prompted by the following awkward
  interaction:

  - The command line interface was initially designed such that killing of
    workers was enabled when thresholds were given as command line options.

  - Since then support for configuration files was added, and given the
    presence of a configuration file thresholds would always be set so
    killing would happen implicitly and unconditionally.

  To solve these `explicit is better than implicit`_ contradictions all in one
  go I decided to make a backwards incompatible change to the command line
  interface, where both of the actions described above now need to be requested
  using command line options.

- Parsing of sizes now uses binary multiples of bytes (base-2) for ambiguous
  unit symbols and names whereas previously decimal multiples of bytes
  (base-10) were used.

- The custom initializer on the main :class:`~apache_manager.ApacheManager`
  class was removed because it was a historical artefact whose significance was
  lost in time.

**Other significant changes:**

- Make the :attr:`~apache_manager.ApacheManager.hanging_worker_threshold`
  option configurable.

- Add support for configuration files to configure killing of workers (for
  details see the :attr:`~apache_manager.ApacheManager.config_loader`
  property).

- Expose native and foreign worker count in data file to enable monitoring that
  detects configuration issues (like the native worker count being lower than
  the foreign worker count, causing the native workers to become saturated).

**Miscellaneous changes:**

- Document that Linux is required (`#2`_).
- Improve string representation of WSGI workers.
- Use Python 3 for local development in ``Makefile``.

.. _Release 2.0: https://github.com/xolox/python-apache-manager/compare/1.2...2.0
.. _explicit is better than implicit: https://www.python.org/dev/peps/pep-0020/#the-zen-of-python
.. _#2: https://github.com/xolox/python-apache-manager/issues/2

`Release 1.2`_ (2019-03-28)
---------------------------

- Added Python 3.6 and 3.7 to test suite and documented support for them (based
  on the fact that the test suite passes).

- Bug fix to improve compatibility with newer Apache versions:

  In Ubuntu 18.04 the plain text server status page response contains multiple
  uptime entries and because the regular expressions used by apache-manager
  weren't anchored to the start of the line, this new status page contents
  confused apache-manager.

  On Ubuntu 14.04:

  .. code-block:: console

     $ curl -s http://localhost/server-status?auto | grep -i uptime
     Uptime: 96606

  On Ubuntu 18.04:

  .. code-block:: console

     $ curl -s http://localhost/server-status?auto | grep -i uptime
     ServerUptimeSeconds: 5163
     ServerUptime: 1 hour 26 minutes 3 seconds
     Uptime: 5163

- Include documentation in source distributions (``MANIFEST.in``).

- Changed Sphinx documentation theme (to the 'nature' theme).

- Added license=MIT to ``setup.py`` script.

.. _Release 1.2: https://github.com/xolox/python-apache-manager/compare/1.1...1.2

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

- Refactor ``WorkerStatus`` class to properly use :pypi:`property-manager`.

  Strictly speaking this change set breaks backwards compatibility, however 99%
  percent of the functionality is the same, I've mostly just changed a whole
  lot of undocumented implementation details. Nevertheless I'm bumping the
  major version number because "explicit is better than implicit".

- Use :pypi:`six` instead of homegrown ``apache_manager.compat`` module.

  Six was already included in the transitive requirements via :pypi:`executor`
  so there was really no point in not using it 🙂.

- Refactor makefile & setup script (checkers, docs, wheels, twine, etc).

- Improve test coverage of port discovery

.. _Release 1.0: https://github.com/xolox/python-apache-manager/compare/0.6...1.0

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

Internal refactoring: Switch from :pypi:`cached-property` to :pypi:`property-manager`.

.. _Release 0.2: https://github.com/xolox/python-apache-manager/compare/0.1.1...0.2

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
