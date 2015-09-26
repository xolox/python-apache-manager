# Monitor and control Apache web server workers from Python.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: September 26, 2015
# URL: https://apache-manager.readthedocs.org

"""
A ``top`` like interactive viewer for Apache web server metrics.

The :mod:`~apache_manager.interactive` module implements a ``top`` like
interactive viewer for Apache web server metrics using curses_. It can be
invoked from the command line using ``apache-manager --watch``.

Please note that the functions in this module are not included in the test
suite and are excluded from coverage calculations because:

1. For now this module is just an interesting experiment. It might disappear
   completely or I might change it significantly, it all depends on time and
   interest. For example it would be cool to have a tool like mytop_ or
   innotop_ for Apache workers, but it's going to take time to build something
   like that and I have 40+ open source projects and limited spare time, so I'm
   not going to commit to anything :-).

2. This is my first time working with Python's :mod:`curses` module (and
   curses_ interfaces in general) and it's not yet clear to me how feasible it
   is to test an interactive command line interface that's not line based.

.. _curses: https://en.wikipedia.org/wiki/Curses_(programming_library)
.. _innotop: https://github.com/innotop/innotop
.. _mytop: http://jeremy.zawodny.com/mysql/mytop/
"""

# Standard library modules.
import curses
import logging
import time

# External dependencies.
import coloredlogs


def watch_metrics(manager):
    """Watch Apache web server metrics in a ``top`` like interface."""
    try:
        curses.wrapper(redraw_loop, manager)
    except KeyboardInterrupt:
        pass


def redraw_loop(screen, manager):
    """The main loop that continuously redraws Apache web server metrics."""
    # Ugly workaround to avoid circular import errors due to interdependencies
    # between the apache_manager.cli and apache_manager.interactive modules.
    from apache_manager.cli import report_metrics, line_is_heading
    # Hide warnings (they'll mess up the curses layout).
    coloredlogs.set_level(logging.ERROR)
    # Hide the text cursor.
    cursor_mode = curses.curs_set(0)
    # Make Control-C behave normally.
    curses.noraw()
    # Enable non-blocking getch().
    screen.nodelay(True)
    try:
        # Repeat until the user aborts.
        while True:
            lnum = 0
            for line in report_metrics(manager):
                attributes = 0
                if line_is_heading(line):
                    attributes |= curses.A_BOLD
                screen.addstr(lnum, 0, line, attributes)
                lnum += 1
            # Redraw screen.
            screen.refresh()
            # Wait a while before refreshing the screen, but enable the user to
            # quit in the mean time.
            for i in range(10):
                if screen.getch() == ord('q'):
                    return
                # Don't burn through CPU like crazy :-).
                time.sleep(0.1)
            # Update metrics in next iteration.
            manager.refresh()
            # Clear screen for next iteration.
            screen.erase()
    finally:
        # Restore cursor mode.
        curses.curs_set(cursor_mode)
        # Clean up the screen after we're done.
        screen.erase()
