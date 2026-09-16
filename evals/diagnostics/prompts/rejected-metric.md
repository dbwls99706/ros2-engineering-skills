# Reconsidering a cleanup setting

This is a synthetic ROS 2 offline map-cleanup review. Candidate evaluation is
allowed; map deployment is not.

The stated requirement is to improve removal of labelled person points while
deleting at most 1% of labelled static-structure points. All rows below use the
same 200 person points and 2,000 static points in a single exploratory recording.

| Setting | Person points removed | Static points removed |
|---|---:|---:|
| Baseline A | 40 | 2 |
| Candidate B | 60 | 3 |
| Candidate C | 190 | 600 |

B was previously rejected because it did not change the residual count in one
doorway. That doorway statistic was never related to the person-removal
requirement. A later summary calls B permanently ruled out and recommends C
because it removes the most person points. No independent confirmation recording
has been evaluated.

Review the two decisions and write the corrected investigation record. State the
basis and limits of the comparison and the next bounded action.
