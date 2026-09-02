# controls_and_confounders — isolating the effect from its impostors

Load this when an effect is observed and about to be attributed to a
cause. The effect has many possible parents; a control is a designed
observation that could have shown the effect was someone else's — and
didn't. An effect with no surviving negative control is not yet
isolated, and an un-isolated effect explained is a story.

## The design

Enumerate the confounders: everything that co-varies with the claimed
cause — selection, environment, instrument drift, batch, phase,
simultaneous changes. The question is not "is there an effect" but
"is the effect the parent I named".

Positive control: a condition that MUST produce the effect. It
validates that the instrument and the procedure can see the effect at
all — a null result next to a failed positive control means nothing
except a broken detector.

Negative control: a condition that MUST NOT produce it — the sham,
the vehicle, the untouched baseline. It is the rung that dies when
the effect is an artifact, which is why it is the one worth running.

Predict from the explanation what should NOT move if the explanation
is right — those predictions are free controls, and checking them
costs nothing but the honesty to look.

Use the field's standing isolation surfaces: the sideband or control
region (physics), the ablation (ML and analysis), the sham condition
(bio), the holdout (data). They exist because the impostors recur.

## The reading

Controls do not prove the cause; they retire the alternatives in
batches. What survives is still a hypothesis — but one that no longer
has a place to hide, which is what attribution requires.

Provenance: textbook control design across physics, ML, and biology;
the sideband and ablation practice of our analysis campaigns.
