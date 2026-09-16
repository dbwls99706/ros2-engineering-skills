# Offline cleanup diagnosis

This is a synthetic ROS 2 map-analysis case. No robot is connected and no map
replacement is authorized.

An engineer wrote an angular-neighborhood ray diagnostic. With a narrow radius
it reports no observation for most retained points, and the investigation note
already concludes: "The cleanup cannot improve; the mapping route must change."
The diagnostic's geometry has not been compared with the cleanup implementation.

A later labelled control set has 20 true intersections and 20 true misses. The
narrow radius detects 2 intersections and reports zero hits on all true misses.
A wider radius detects all 20 intersections but also reports hits on 18 true
misses. A separate check finds that the cleanup's removed points are all detected
by the wide diagnostic. The labels for that separate check came from cleanup
output alone, not independently measured geometry.

Assess the existing conclusion and the wider diagnostic. Write the next scoped
investigation update and a bounded next test. Do not invent additional results.
