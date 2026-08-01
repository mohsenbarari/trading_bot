"""Repository-local operational helpers.

This marker deliberately makes ``scripts`` a regular package rather than a
namespace package.  Emergency publisher and receiver imports must resolve to
the checked-in local files, never to a similarly named directory supplied by
an ambient ``PYTHONPATH``.
"""
