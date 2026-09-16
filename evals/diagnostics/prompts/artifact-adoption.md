# Saved map investigation

This is a synthetic offline ROS 2 artifact review. Do not write to the installed
map or replay a bag into a live graph.

A cleanup helper returns `applied=true`. The caller's log then says the candidate
failed its acceptance check and the writer selected the original map. The saved
map and the candidate each contain 100 points. Comparing their removed input IDs
on the same original input yields A={1,2} and B={1,3}. One intermediate stage
resamples coordinates and drops original IDs; only a radius-based correspondence
is available for that stage, and duplicate coordinates occur.

An older map has matching coordinates, but there is no processing manifest or
invocation record connecting it to this run. An unmodified rebuilt helper has a
different binary hash from the installed one, yet produces the same removed IDs
on this one recording.

What can be concluded about backend adoption, result equivalence, and historical
lineage? Give the smallest read-only checks needed to resolve the remaining
questions and state what the rebuild comparison does and does not establish.
