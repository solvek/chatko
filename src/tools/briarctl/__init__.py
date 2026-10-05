"""briarctl: the admin's command-line tool for the hub's Briar account (design.md §7.5, D24).

A separate program: it imports nothing of chatko, and chatko never imports it. It talks only to
the `briar-headless` REST API.
"""
