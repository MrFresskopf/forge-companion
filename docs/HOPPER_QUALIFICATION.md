# Remote-hopper mechanical qualification

This protocol records supervised tests of the complete installed Fermzilla, magnet, cable, and winch assembly. It does not calculate or certify a safe pulse duration and never authorizes remote firing.

Copy [`templates/hopper-qualification.csv`](templates/hopper-qualification.csv) to a private working location before recording trials. Qualification records may reveal brewery operations and should not be committed unless deliberately sanitized.

## Prerequisites

Before every supervised trial:

- install the complete production-load assembly;
- confirm a manual electrical isolation method is immediately available;
- confirm the DPDT direction switch is in the intended position;
- verify free cable travel and a protected mechanical endpoint;
- keep personnel clear of the cable, winch, magnet, and moving parts;
- use a deliberately selected short pulse and an independent device-side auto-off;
- obtain explicit immediate approval before any real hardware command.

The current software ceiling and any existing device auto-off are not qualified mechanical runtimes.

## Current unloaded prototype evidence

The current winch completed one supervised one-second pulse in each direction without a magnet or
other load. Each pulse produced about 3 cm of travel, stopped cleanly, and returned electrical telemetry
to `OFF`; the reverse pulse returned the winch to approximately its starting position. Forge Companion
therefore caps every live Cloud pulse at 1,000 ms. The prototype Shelly's separately configured
four-second auto-off corresponds to an estimated 12 cm of unloaded travel and remains an emergency
backstop only. Forge Companion cannot verify that device configuration through the Cloud fire path.

This evidence does not qualify magnet release, loaded cable behavior, installed geometry, endpoint
safety, or hop addition. The ten full-assembly trials below remain required once the complete assembly
is available.

## Record every trial

Use `yes`, `no`, or `unknown` consistently for boolean columns. Record:

- requested pulse and device auto-off durations;
- required and actual travel under load;
- whether the magnet fully released;
- whether the mechanism reached an endpoint, jammed, or stalled;
- whether electrical telemetry returned to `OFF`;
- whether hop release was visually confirmed;
- any noise, cable behavior, delay, or intervention.

Electrical `OFF` is not evidence of travel, magnet release, or hop addition.

## Minimum evidence before considering remote operation

- at least ten consecutive full-assembly trials under representative load;
- ten of ten complete magnet releases;
- no jam, stall, unsafe cable tension, or mechanical-end impact;
- measured travel and timing remain repeatable;
- manual isolation and independent device-side auto-off are verified;
- a conservative pulse duration is chosen by the operator from the observed evidence;
- the final remote attempt still receives a fresh online/OFF preflight and immediate human approval.

Any changed magnet, cable routing, vessel geometry, winch, load, direction, or endpoint invalidates the prior qualification and requires a new series.
