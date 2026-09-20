"""Operator-side isolation, authorization and release auditing (P21).

This package implements the protected path: sealed-mode readiness, final
request/receipt handling, bounded operator execution and release audits. The
ordinary development workspace is development-only by default; nothing here
places real final data or keys in the repository.
"""
