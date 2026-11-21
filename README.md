# Burrow Independent

Burrow Independent pilots a mech in the supplied Mech Royale game. Its shelter planner considers approaching zones, resources, travel cost and the directional visibility of enemy actors. It shares the 32-direction map table and shelter search with Burrow. Its independent controller follows waypoints using velocity feedback, responds to damage and sound cues, manages equipment and weapons, and uses short jet bursts when stalled. It carries the starter gun and can equip a laser or lightning pickup.

The entry includes the official Python transport, the planner, its map table and the controller. Runtime needs only the Python standard library. Build the directory with `docker build -t potato-burrow-independent .` and run the image on the Mech Royale JSON-lines protocol.
