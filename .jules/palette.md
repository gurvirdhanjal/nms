## 2026-03-10 - Screen reader accessibility on icon buttons
**Learning:** Icon-only buttons in table action rows (like Edit, Play/Pause, Delete) often rely solely on visual cues and lack an `aria-label` which breaks screen reader accessibility. I added `aria-label` to these buttons in templates like `user_management.html` and `ssh_profiles.html`.
**Action:** Always verify that every icon-only button contains an `aria-label` that descriptively explains its action. This is a quick win for accessibility that doesn't impact visual design.
