# Remote-hopper mechanical qualification

This protocol records supervised tests of the complete installed Fermzilla, magnet, cable, and winch assembly. It does not calculate or certify a safe pulse duration and never authorizes remote firing.

The optional [`templates/hopper-qualification.csv`](templates/hopper-qualification.csv) can be copied to a private working location when detailed trial records are wanted. Qualification records may reveal brewery operations and should not be committed unless deliberately sanitized.

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

## Current loaded dry-fixture evidence

A supervised dry fixture placed an 88 g representative load on two inner sous-vide magnets coupled to
two outer hook magnets through a plastic wall. A cable winch pulled both outer magnets through linked
hooks while a Shelly device enforced the selected timer. This was not the installed Fermzilla assembly
and does not count toward the ten full-assembly trials.

The fixture produced the following characterization evidence:

- approximately four separate one-second pulses were required to release the original linked-magnet
  geometry;
- the run initially described as a four-second success was later clarified to have required two
  four-second pulses, so it was a failed one-shot rather than a qualifying success;
- changing the connection to load the magnets sequentially still did not release the load within one
  four-second pulse;
- no observed loaded run established a successful, repeatable four-second one-shot;
- every observed timer completion returned Cloud telemetry to `OFF`, with no reported hard endpoint,
  abnormal noise, cable winding failure, or thermal anomaly;
- video of the two-pulse run showed little visible magnet motion during the first pulse and an abrupt
  release late in the second pulse.

These results reject four seconds as a repeatable one-shot duration for this fixture. There are zero
qualifying one-shot successes, and no run belongs in a ten-trial qualification series. The observations
do not justify increasing the runtime: prolonged drive against a nearly stationary magnetic assembly
may indicate high breakaway force, winch stall, supply sag, cable or drum slip, or compliance in the
linked hooks. Electrical `OFF` does not resolve those mechanical uncertainties.

Before another powered trial, the mechanism should produce a deliberate peel rather than drag both
magnet faces in shear. A non-magnetic ramp may be fixed just beyond the starting position so the first
few millimetres of travel lift one magnet edge away from the wall. Staggered ramps or unequal,
non-elastic links should release the magnets sequentially. Verify the revised geometry repeatedly by
hand with power isolated before requesting one new supervised pulse. Measure the peak peel force over
at least five hand pulls, confirm adequate winch-force margin, verify motor voltage and current under a
brief controlled load without intentional stall, and install a protected mechanical stop. Changing this
geometry starts a new qualification series.

## Optional detailed trial record

When using the CSV, use `yes`, `no`, or `unknown` consistently for boolean columns. Each row
represents one reset fixture and exactly one selected pulse. If that pulse does not complete the release,
record the row as failed; a later retry is a separate diagnostic intervention and never turns the row into
a success. Record:

- requested pulse and device auto-off durations;
- required and actual travel under load;
- whether the magnet fully released;
- whether the mechanism reached an endpoint, jammed, or stalled;
- whether electrical telemetry returned to `OFF`;
- whether hop release was visually confirmed;
- any noise, cable behavior, delay, or intervention.

Electrical `OFF` is not evidence of travel, magnet release, or hop addition.

## Software attestation gate

Forge Companion does not inspect or store ten individual trial outcomes. After the operator has actually
completed ten successful full-assembly tests, `hopper qualification attest` asks for one explicit
declaration and stores its statement version and timestamp in non-secret local preferences. This is an
operator attestation only, not automatic, sensor-based, or independent verification. Revoke it whenever
the installed mechanism or any declared safety assumption changes.

## Minimum evidence before considering remote operation

- at least ten consecutive full-assembly trials under representative load;
- ten of ten complete magnet releases;
- no jam, stall, unsafe cable tension, or mechanical-end impact;
- measured travel and timing remain repeatable;
- manual isolation and independent device-side auto-off are verified;
- a conservative pulse duration is chosen by the operator from the observed evidence;
- the final remote attempt still receives a fresh online/OFF preflight and immediate human approval.

Any changed magnet, cable routing, vessel geometry, winch, load, direction, or endpoint invalidates the prior qualification and requires a new series.
