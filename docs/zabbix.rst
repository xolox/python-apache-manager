.. highlight:: console
.. _Zabbix integration for apache-manager:

Zabbix integration for apache-manager
=====================================

This document explains how to integrate :pypi:`apache-manager` in the Zabbix_
monitoring system. These instructions were written for Ubuntu_ which is based
on Debian_ and are expected to work for most if not all of the 140+ Debian
derivatives_.

.. contents::
   :depth: 2
   :local:

Configure the Zabbix server
---------------------------

The file `zabbix/template.xml`_ can be imported from the Zabbix
server web interface and defines the necessary bits and pieces:

- Discovery rules for WSGI daemon process groups.
- Items that provide Apache server metrics (extracted from the status page).
- Items that provide Apache Manager metrics (whether the status page was
  successfully fetched and the number of native and foreign workers).
- Triggers to alert about metrics that indicate problems.

The item intervals in the template have been set to 5 minutes and the discovery
interval is set to 30 minutes. Make sure the item interval matches the interval
of the cron job that runs the ``apache-manager --collect-metrics`` command.

.. note:: The template contains ``log.count[]`` items which require a Zabbix
          agent version newer than 3.2 to work.

Configure the Zabbix client
---------------------------

1. Make sure ``/etc/zabbix/zabbix_agentd.conf`` contains the following line:

   .. code-block:: sh

      Include=/etc/zabbix/zabbix_agentd.d/*.conf

2. Create the file ``/etc/zabbix/zabbix_agentd.d/apache-manager.conf``:

   .. literalinclude:: ../zabbix/client.conf
      :language: sh

3. Make sure the Zabbix agent can read the system log::

    $ sudo gpasswd -a zabbix adm

3. Restart the Zabbix agent to activate the changes::

    $ sudo service zabbix-agent restart

Install the program
~~~~~~~~~~~~~~~~~~~

Installing Python packages on your Linux servers can be accomplished in quite a
few ways with specific pro's and con's to each method, here are some examples:

.. contents::
   :local:

System wide install
+++++++++++++++++++

If you're not worried about version conflicts you can install apache-manager
system wide::

 $ sudo pip install apache-manager

.. note:: Depending on your installation you may need to use :man:`pip3`
          instead of :man:`pip` to install under Python 3 instead of 2.

Custom install root
+++++++++++++++++++

If you are worried about version conflicts you can install the program and its
dependencies in an isolated directory::

 $ sudo pip install --prefix=/opt/apache-manager apache-manager

Change ``pip`` to ``pip3`` if applicable. To run the program you set
``$PYTHONPATH`` to the location of the ``site-packages`` directory inside the
custom install root and execute the ``python -m apache_manager ..`` command. To
make this a bit more practical to use you can create a shell script wrapper::

 $ sudo vim /usr/bin/apache-manager

Enter the following contents:

.. code-block:: sh

   #!/bin/bash -e

   export PYTHONPATH=$(echo /opt/apache-manager/lib/python*.*/site-packages)
   exec python -m apache_manager "$@"

Change ``python`` to ``python3`` if applicable. The ``exec`` keyword ensures
that the :man:`timeout` command used in :ref:`the cron job below <Configure
cron>` applies to the Python interpreter instead of the shell that launched it.
Don't forget to make the script executable::

 $ sudo chmod a+x /usr/bin/apache-manager

Virtual environment
+++++++++++++++++++

I advice against the use of virtual environments for deployments and consider
them a development tool instead, because security updates to the system wide
Python installation can break virtual environments (whereas the use of a custom
install root has no such problem). If you're aware of the pro's and con's but
prefer it anyway::

 $ sudo virtualenv /opt/apache-manager
 $ sudo /opt/apache-manager/bin/pip install apache-manager

You can use a symbolic link to make the program available on the default system
wide executable search path::

  $ sudo ln -s /opt/apache-manager/bin/apache-manager /usr/bin/apache-manager

.. _Configure cron:

Configure cron
~~~~~~~~~~~~~~

Create the file ``/etc/cron.d/apache-manager`` with the following contents:

.. code-block:: sh

   # /etc/cron.d/apache-manager:
   # Cron job to monitor and control Apache.

   # Start with a sane $PATH (feel free to customize this).
   PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

   # Run the program every five minutes, with a timeout of 30 seconds.
   */5 * * * * root timeout 30 apache-manager --collect-metrics --kill-workers --quiet

Some notes about this cron job:

Customizing the interval
 The ``*/5`` expression means the command will be run every five minutes, this
 is the part you will want to change if you'd like a different interval. For
 example to run the command every minute you would use ``*`` instead of
 ``*/5``. Read up on `specifying cron intervals`_ for more details.

Customizing the user
 The command is run as the ``root`` user to enable detection of WSGI process
 group names during metrics collection and to kill workers that exceed resource
 usage thresholds. If these features are not important to you then feel free to
 change this to a different user for improved security.

Timeout handling
 If your server is overloaded and for one reason or another Apache is not
 responding at http://localhost/server-status the ``apache-manager`` program
 will hang and Python processes may stack up, exacerbating the problem. To
 prevent this the :man:`timeout` program is prefixed to the command line.

Killing of workers
 The command line option ``--kill-workers`` is only useful when you create an
 apache-manager configuration file that defines criteria for the killing of
 Apache workers that exceed resource usage thresholds. If you include
 ``--kill-workers`` without having configured resource usage thresholds this is
 not considered an error, because it makes it possible to deploy apache-manager
 to a large number of servers where only some of those servers opt-in to
 killing of workers.

Configure sudo
~~~~~~~~~~~~~~

The Zabbix agent configuration given above uses :man:`sudo` to enable WSGI
process group discovery because super user privileges are required to read
process names from ``/proc``, here's how to configure this:

1. Use :man:`visudo` to create the new configuration file::

    $ sudo visudo -f /etc/sudoers.d/apache-manager

   This protects against shooting yourself in the foot by breaking :man:`sudo`
   due to incorrect file permissions 😇.

2. Add the following contents to the file:

   .. code-block:: sh

      # /etc/sudoers.d/apache-manager:
      #
      # Allow Zabbix to discover WSGI process groups.

      zabbix ALL=NOPASSWD: /usr/bin/apache-manager --zabbix-discovery

   This configuration makes two assumptions that you should verify or adjust:

   1. The Zabbix agent is running as the system user ``zabbix``.
   2. The command line program is installed as ``/usr/bin/apache-manager``.

3. You can verify that things work as intended by switching to the system user
   that is used by the Zabbix agent::

    $ sudo -u zabbix -i

   If you now run the following command, it should not prompt for a password::

    $ sudo apache-manager --zabbix-discovery

.. _Debian: https://debian.org/
.. _derivatives: https://en.wikipedia.org/wiki/Debian#Derivatives_and_flavors
.. _specifying cron intervals: https://en.wikipedia.org/wiki/Cron#Overview
.. _Ubuntu: https://ubuntu.com/
.. _zabbix/template.xml: https://github.com/xolox/python-apache-manager/blob/master/zabbix/template.xml
.. _Zabbix: https://www.zabbix.com/
