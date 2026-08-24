"""The Supervisor: the one who pays for seats.

Owns its facts (facts.py — batch and runtime facts assembled at wake
time), its agent (agent.py — the growth gate session), and its worker
entry (cli.py).  Imports shared agent infrastructure FROM the scientist
package; the harness never imports back.
"""
