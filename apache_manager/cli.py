# Monitor and control Apache web server workers from Python.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: February 27, 2020
# URL: https://apache-manager.readthedocs.io

"""
Usage: apache-manager [OPTIONS]

Command line interface to monitor the Apache web server and kill worker
processes that exceed resource thresholds. When no options are given the
server metrics and memory usage of workers are printed to the terminal.

Supported options:

  -c, --collect-metrics

    Collect monitoring metrics and store them in a text file to be read
    by a monitoring system like Zabbix. See also the --data-file option.

  -k, --kill-workers

    Kill Apache workers exceeding the thresholds given by --max-memory-active,
    --max-memory-idle and --max-time. These thresholds can also be defined in
    configuration files, please refer to the online documentation for details.
    See also the --dry-run option.

  -w, --watch

    This option causes the Apache manager to redraw the collected metrics once
    every 10 seconds in a `top' like interface until interrupted using `q' (for
    quite) or Control-C.

  -a, --max-memory-active=SIZE

    Kill active Apache workers that are using more memory than specified by the
    SIZE argument. SIZE is expected to be a human readable memory size like 50K
    (50 kilobytes), 42M (42 megabytes), 2G (2 gigabytes), etc.

  -i, --max-memory-idle=SIZE

    Kill Apache workers that are using more memory than specified by the SIZE
    argument (see --max-memory-active for acceptable values of SIZE).

  -t, --max-ss, --max-time=TIMESPAN

    Kill Apache workers whose "time since the beginning of the most recent
    request" is greater than specified by the TIMESPAN argument. TIMESPAN is
    expected to be a human readable timespan like 2s (2 seconds), 3m (3
    minutes), 5h (5 hours), 2d (2 days), etc.

  -T, --hanging-worker-threshold=TIMESPAN

    Change the number of seconds before an active worker is considered hanging
    to TIMESPAN (see --max-time for acceptable values of TIMESPAN).

  -f, --data-file=PATH

    Change the pathname of the file where the Apache manager stores monitoring
    metrics after every run. Defaults to `/tmp/apache-manager.txt'.

  -z, --zabbix-discovery

    Generate a JSON fragment that's compatible with the low-level discovery
    support in the Zabbix monitoring system. With the right template in place
    this enables the Zabbix server to discover the names of the WSGI process
    groups that are active on any given server. This makes it possible to
    collect and analyze the memory usage of specific WSGI process groups.

  -n, --dry-run, --simulate

    Don't actually kill any Apache workers.

  -v, --verbose

    Increase verbosity (can be repeated).

  -q, --quiet

    Decrease verbosity (can be repeated).

  -h, --help

    Show this message and exit.
"""

# Standard library modules.
import getopt
import json
import logging
import sys

# External dependencies.
import coloredlogs
from humanfriendly import (
    format_size,
    format_timespan,
    parse_size,
    parse_timespan,
    pluralize,
)
from humanfriendly.terminal import HIGHLIGHT_COLOR, ansi_wrap, output, usage, warning

# Modules included in our package.
from apache_manager import ApacheManager, NATIVE_WORKERS_LABEL
from apache_manager.interactive import watch_metrics

# Initialize a logger for this program.
logger = logging.getLogger(__name__)


def main():
    """Command line interface for the ``apache-manager`` program."""
    # Configure logging output.
    coloredlogs.install(syslog='notice')
    # Command line option defaults.
    actions = set()
    kw = dict()
    data_file = '/tmp/apache-manager.txt'
    dry_run = False
    # Parse the command line options.
    try:
        options, arguments = getopt.getopt(sys.argv[1:], 'ckwa:i:t:T:f:znvqh', [
            'collect-metrics', 'kill-workers', 'watch', 'max-memory-active=',
            'max-memory-idle=', 'max-ss=', 'max-time=',
            'hanging-worker-threshold=', 'data-file=', 'zabbix-discovery',
            'dry-run', 'simulate', 'verbose', 'quiet', 'help',
        ])
        for option, value in options:
            if option in ('-c', '--collect-metrics'):
                actions.add('collect')
            elif option in ('-k', '--kill-workers'):
                actions.add('kill')
            elif option in ('-w', '--watch'):
                actions.add('watch')
            elif option in ('-a', '--max-memory-active'):
                kw['max_memory_active'] = parse_size(value, binary=True)
            elif option in ('-i', '--max-memory-idle'):
                kw['max_memory_idle'] = parse_size(value, binary=True)
            elif option in ('-t', '--max-ss', '--max-time'):
                kw['worker_timeout'] = parse_timespan(value)
            elif option in ('-T', '--hanging-worker-threshold'):
                kw['hanging_worker_threshold'] = parse_timespan(value)
            elif option in ('-f', '--data-file'):
                data_file = value
            elif option in ('-z', '--zabbix-discovery'):
                actions.add('discovery')
            elif option in ('-n', '--dry-run', '--simulate'):
                logger.info("Performing a dry run ..")
                dry_run = True
            elif option in ('-v', '--verbose'):
                coloredlogs.increase_verbosity()
            elif option in ('-q', '--quiet'):
                coloredlogs.decrease_verbosity()
            elif option in ('-h', '--help'):
                usage(__doc__)
                return
        if arguments:
            raise Exception("This program doesn't support any positional arguments")
    except Exception as e:
        warning("Error: %s!", e)
        sys.exit(1)
    manager = ApacheManager(**kw)
    try:
        # Execute the requested action(s).
        if 'kill' in actions:
            manager.kill_workers(dry_run=dry_run)
        if 'watch' in actions:
            watch_metrics(manager)
        if 'discovery' in actions:
            report_zabbix_discovery(manager)
        # Render a summary of monitoring metrics when no action was requested.
        if not actions and data_file != '-':
            for line in report_metrics(manager):
                if line_is_heading(line):
                    line = ansi_wrap(line, color=HIGHLIGHT_COLOR)
                output(line)
    except Exception:
        logger.exception("Encountered unexpected exception, aborting!")
        sys.exit(1)
    finally:
        if 'collect' in actions and (data_file == '-' or not dry_run):
            manager.save_metrics(data_file)


def report_metrics(manager):
    """Create a textual summary of Apache web server metrics."""
    lines = ["Server metrics:"]
    for name, value in sorted(manager.server_metrics.items()):
        if name in ('total_traffic', 'bytes_per_second', 'bytes_per_request'):
            value = format_size(value)
        elif name == 'cpu_load':
            value = '%.1f%%' % value
        elif name == 'uptime':
            value = format_timespan(value)
        name = ' '.join(name.split('_'))
        name = name[0].upper() + name[1:]
        lines.append(" - %s: %s" % (name, value))
    main_label = "main Apache workers" if manager.wsgi_process_groups else "Apache workers"
    report_memory_usage(lines, main_label, manager.memory_usage)
    for name, memory_usage in sorted(manager.wsgi_process_groups.items()):
        report_memory_usage(lines, "WSGI process group '%s'" % name, memory_usage)
    return lines


def report_memory_usage(lines, label, memory_usage):
    """Create a textual summary of Apache worker memory usage."""
    lines.append("")
    workers = pluralize(len(memory_usage), "worker")
    lines.append("Memory usage of %s (%s):" % (label, workers))
    lines.append(" - Minimum: %s" % format_size(memory_usage.min))
    lines.append(" - Average: %s" % format_size(memory_usage.average))
    lines.append(" - Maximum: %s" % format_size(memory_usage.max))


def report_zabbix_discovery(manager):
    """Enable Zabbix low-level discovery of WSGI application groups."""
    worker_groups = [NATIVE_WORKERS_LABEL] + sorted(manager.wsgi_process_groups.keys())
    output(json.dumps({'data': [{'{#NAME}': name} for name in worker_groups]}))


def line_is_heading(line):
    """Check whether a line of output generated by :func:`report_metrics()` should be highlighted as a heading."""
    return line.endswith(':')
