Matsu's Tracker  (by MatsuHikaru)
=================================
A small always-on-top window that reads PokeMMO's chat log and shows the
Halloween callouts live: swarms per region, Kanto kids and the miniboss.

SETUP (one time)
----------------
1. In PokeMMO go to Settings -> Chat and turn ON "Log chat to disk".
   (The tracker can only see what PokeMMO writes to its chat log.)
2. Double-click "Matsu's Tracker.exe".
   - Windows may show "Windows protected your PC" because the app isn't signed.
     Click "More info" -> "Run anyway".
   - If your PokeMMO isn't in a usual folder, it asks you to pick it:
     choose the folder that contains PokeMMO.exe.
3. Optional: Settings -> "Set my character names..." and enter your in-game name.
   (It also learns your name automatically from your first wild battle.)

To start it easily later: right-click the .exe -> Show more options ->
Send to -> Desktop (create shortcut).

USING IT
--------
- Status dot: green = all good. Yellow = a teammate asked in Team chat for
  kids/MB/swarm locations, or all 3 Kanto kids are confirmed and you haven't
  shared them yet. Click "copy" (or the dot) to clear it.
- copy: puts a short summary on your clipboard, ready to paste in chat.
- Event menu: pick the event (Halloween is the one set up so far).
- View menu: see-through level.
- on top: keep the window above the game, or not.
- Settings -> Callout formats...: if a caller's callouts don't show up,
  paste one of their lines there to see what the tracker reads, and add
  the label they use (e.g. "Swarm list:") as an extra tag.

It only READS the chat log file on your PC. It doesn't touch the game,
send anything, or need your login. Settings are saved in
%APPDATA%\Matsu's Tracker\settings.json

Source code: https://github.com/antoaad1000-glitch/matsus-tracker
