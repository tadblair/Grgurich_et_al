# Data

Everything in this directory describes the 47 rats of the manuscript cohort
(`../config/paper_cohort.yaml`) and nothing else. Two inputs, one output:

| Directory | What | Produced by |
|---|---|---|
| `raw/MazeControl.db` | the rig's SQLite database: subjects, sessions, trials and the event log, exactly as the maze-control software wrote them | the rig |
| `tracking/<RAT>.parquet` | per-frame position and maze zone for every recorded session, one file per rat | the development pipeline, from the rig's per-frame CSVs (see below) |
| `processed/` | the cleaned database and the tables the figure notebooks read | `../data/pipeline/run_pipeline.py`, from the two above |

`processed/` is committed so the notebooks run without a rebuild; `python data/pipeline/run_pipeline.py`
regenerates it in about 23 s and `python figures/scripts/verify.py --rebuild` confirms
the regenerated tables equal the committed ones.

## What was done upstream

- **The raw database is the lab's, filtered.** The rig's database holds every animal the lab
  has run. The copy here keeps the rows belonging to the 47 cohort rats and deletes
  the rest; nothing else is altered. Sessions the experimenter voided at the time
  (`session_number = 'X'`, or a null duration after an aborted start) are still present, and
  Stage 1 removes them in the open. File-name columns keep the paths of the rig computer.
- **The tracking was cleaned before it got here.** The rig wrote one CSV of per-frame
  coordinates per recording. The development repository read those files, interpolated the
  handful of corrupt rows, stitched the three sessions that were recorded in two parts
  (`source_segment`), and assigned every frame a maze zone from its x/y position with the
  map in `../src/corner_maze/common/zones.py`. Those steps need the CSVs, which are not part
  of this release; their result is. The zone map ships, so `zone` can be recomputed from
  `x` and `y` and checked.
- **One cohort session has no tracking:** CM008 Exposure session 1e (`session_id` 1456, 2023-07-01). No figure uses it.

## Limits worth knowing

- **`errors` is rig-logged.** Every accuracy score in the paper rests on the rig's own count
  of wrong-well entries (`trials.errors == 0` means the first well entered was the goal).
  Counting the well visits reconstructed from the tracking (`processed/trial_well_visits.parquet`)
  that were not the reward visit gives the same number on 94.5 % (12,925 of 13,683) of the trials
  with at least one reconstructed visit; both are here so the disagreement can be examined,
  but the rig's count is the one the paper uses.
- **Exposure sessions.** The two pre-training exposure sessions per rat appear in `sessions`
  and their rewards are reconstructed in `processed/exposure_rewards.parquet` because the
  pipeline ships whole. That reconstruction is approximate (the rig logged exposure rewards
  as trial rows without a reliable time stamp) and no result in the paper uses it.

## `raw/MazeControl.db`

SQLite. Four tables as the rig wrote them (row counts for the cohort). Dates are M/D/YYYY text; times are HH:MM:SS text; the `*_file` columns are paths on the rig computer.


### `subjects` (47 rows)

| Column | Type | Description |
|---|---|---|
| subject_id | integer | Rig primary key; the `subject_id` used in every other table |
| name | text | Rat name (CM000 … CM064) |
| sex | text | M or F |
| behavior | text | Rig task family; always "Landmark Guided Task" here |
| last_session | text | Number of the most recent session at the time the database was exported |
| cue_goal_orientation | text | Fixed cue-monitor / goal-corner pairing for the rat, e.g. N/NE |
| session_type | text | Session type the rig would run next (rig bookkeeping) |
| active | integer | Rig flag: rat still in training |
| state | text | Rig bookkeeping; empty |
| reward_volume | integer | Reward volume (µl) |
| delay | integer | Rig delay parameter (s) |
| date_of_birth | text | Date of birth (M/D/YYYY) |

### `session` (587 rows)

| Column | Type | Description |
|---|---|---|
| session_id | integer | Rig primary key |
| session_number | text | Position in the training sequence ("1" … "26"), "1e"/"2e" for the two exposure sessions, or "X" for a session the experimenter voided (dropped by Stage 1) |
| date | text | Session date (M/D/YYYY) |
| time | text | Session start time |
| behavior | text | Rig task family |
| session_type | text | Rig session type (training condition and phase); mapped to experiment phases by Stage 3a |
| cue_conf | text | Cue-monitor / goal-corner pairing in force for the session, e.g. N/NW |
| session_duration | real | Duration in seconds; null when the rig was stopped before the session completed |
| total_trials | integer | Trials run |
| total_perfect | integer | Trials with `errors = 0` |
| score | text | total_perfect / total_trials, as text |
| total_errors | integer | Sum of `errors` over trials |
| average_errors | real | Mean errors per trial |
| seed | integer | Rig random seed for the trial sequence |
| conf_file | text | Rig configuration file (empty) |
| coordinate_history_file | text | Path on the rig computer of the per-frame tracking CSV (the source of `data/tracking/`) |
| session_event_history_file | numeric | Path on the rig computer of the event CSV (the same rows as `session_event`) |
| video_file | text | Path on the rig computer of the session video (not part of this release) |
| subject_id | integer | Foreign key to `subjects` |
| reward_volume | integer | Reward volume (µl) |
| average_latency | integer | Mean trial latency (s) |
| score_string | text | Rig bookkeeping; empty |
| total_switches | integer | Rig bookkeeping |
| delay | integer | Rig delay parameter (s) |

### `trial` (16,733 rows)

| Column | Type | Description |
|---|---|---|
| trial_number | integer | 1-based trial index within the session |
| start_arm | text | Arm the rat was released from (North / South / East / West) |
| goal_location | text | Rewarded corner (Northeast / Northwest / Southeast / Southwest) |
| cue_orientation | text | Position of the lit cue monitor, or OFF |
| time_duration | integer | Seconds from door opening to reward |
| errors | integer | Rig-logged number of wrong-well entries before the goal; 0 = the first well entered was the goal |
| session_type | text | Copied from the session |
| trial_id | integer | Rig primary key |
| session_id | integer | Foreign key to `session` |
| turn_1 | text | Rig-logged direction (L / R) of the first turn |
| turn_2 | text | Rig-logged direction (L / R) of the second turn |
| perseveration_score | integer | Rig bookkeeping (probe sessions) |
| first_error | integer | Rig bookkeeping |
| cue_on | integer | 1 if a cue monitor was lit during the trial, else 0 |
| goal_zones_visited | text | Rig-logged sequence of goal zones visited (sparse); Stage 3c reconstructs it from the tracking |

### `session_event` (59,515 rows)

| Column | Type | Description |
|---|---|---|
| session_event_id | integer | Rig primary key |
| action_vector_idx | integer | Position in the rig's per-session action vector |
| action_vector_type | text | Event kind: Presession, Pretrial, Trial Start, Trial End, ITI, Exp Trial (exposure reward), Exp Barrier (exposure barrier move) |
| frame | integer | Video frame index at the event |
| time_stamp | integer | Milliseconds since the session started |
| zone | integer | Maze zone of the rat at the event (`src/corner_maze/common/zones.py`) |
| x_coordinate | integer | Tracked x position at the event (pixels) |
| y_coordinate | integer | Tracked y position at the event (pixels) |
| time_duration | integer | Seconds since the previous event |
| start_arm | text | Trial configuration in force at the event |
| goal_location | text | Trial configuration in force at the event |
| cue_orientation | text | Trial configuration in force at the event |
| session_id | integer | Foreign key to `session` |

## `tracking/<RAT>.parquet`

One file per rat, all of its recorded sessions, sorted by `session_id`, `t_ms`. The same schema for every file; zstd-compressed Parquet.

| Column | Type | Description |
|---|---|---|
| session_id | int32 | FK to sessions |
| frame_idx | int32 | 0-based frame index within session |
| t_ms | int32 | Timestamp in milliseconds from session start |
| x | float | X coordinate (0-239). Room axes: x=0 is south, x=239 is north |
| y | float | Y coordinate (0-239). Room axes: y=0 is west, y=239 is east |
| zone | int8 | Zone ID (1-21, or 0 if unclassified). Fixed to room, does not rotate with task frame |
| source_segment | int8 | Which segment this sample came from (for merged sessions) |

## `processed/`

Written by `data/pipeline/run_pipeline.py`. `MazeControl-clean.db` is the raw database after Stage 1 (voided sessions removed, split recordings merged, a `session_segments` table added). `coordinates.parquet` is the tracking reassembled into one file and is not committed. The tables below are; the notebooks read `subjects`, `sessions` and `trials`.


### `subjects.parquet` (47 rows)

| Column | Type | Description |
|---|---|---|
| subject_id | int16 | Primary key |
| name | large_string | Rat name (e.g. "CM001") |
| sex | large_string | "M" or "F" |
| cue_goal_orientation | large_string | Fixed cue-goal pairing for this rat (e.g. "N/NE"). Defines the spatial relationship between cue monitor and goal corner — the whole frame rotates rigidly so cue and goal maintain relative position |
| date_of_birth | null | Date of birth |
| training_group | dictionary<values=string, indices=int8, ordered=0> | Acquisition training condition: PI+VC / PI+VC_f1 / PI / VC / VC_DREADDs. Derived in stage 3a from acquisition session types |
| novel_route_probe | dictionary<values=string, indices=int8, ordered=0> | Novel-route probe condition: PI_PI / PI_PI+VC / VC_VC / VC_PI+VC. NaN if the rat ran no novel-route probe (stage 3a) |
| reversal_probe | dictionary<values=string, indices=int8, ordered=0> | Reversal probe condition: PI^PI / PI^PI+VC / VC^VC / VC^PI+VC. NaN if no reversal probe; Fixed Cue Switch mapping depends on training_group (stage 3a) |
| approach_to_goal | dictionary<values=string, indices=int8, ordered=0> | Whether the goal lies in the same hemifield as the cue ("toward") or the opposite ("away"). NaN for dark-trained (PI) rats, which have no cue (stage 3a) |

### `sessions.parquet` (556 rows)

| Column | Type | Description |
|---|---|---|
| session_id | int64 | Primary key |
| subject_id | int16 | FK to subjects |
| session_number | large_string | Training sequence: "1"-"26" for training, "1e"/"2e" for exposure |
| session_type | large_string | Condition | n |
| date | date32[day] | Session date |
| time | large_string | Session start time |
| session_duration | float | Duration in seconds |
| total_trials | int16 | Number of trials in session |
| total_perfect | int16 | Trials with no errors |
| total_errors | int16 | Total error count across all trials |
| total_switches | double | Total switches (nullable) |
| score | large_string | Score string from MazeControl |
| average_errors | float | Mean errors per trial |
| average_latency | float | Mean trial latency (seconds) |
| seed | int64 | Random seed used by MazeControl |
| reward_volume | double | Reward volume in ul (nullable) |
| delay | double | Delay parameter (seconds, nullable) |
| cue_conf | large_string | Cue-goal pairing for this session (e.g. "N/NE"). Matches subject's cue_goal_orientation for fixed-frame sessions; differs for Rotate Train / Reversal where the frame rotates |
| sample_rate_hz | double | Coordinate tracking rate (15 or 30 Hz), nullable |
| is_merged | bool | Whether this session was merged from two recordings (an aborted start re-run) |
| n_segments | int8 | Number of recordings backing this session (1 or 2) |
| session_experiment_phase | dictionary<values=string, indices=int8, ordered=0> | Experiment phase: Acquisition / Exposure / Novel Route / Reversal / Rotation / No Cue. Derived in stage 3a from session_type |
| session_order | int16 | Chronological rank of the session within its subject (1 = first), by date and time. Order on this: `session_number` is a string ("1e", "2e", "1" … "26") and sorts lexically. Stage 3a |

### `trials.parquet` (16,398 rows)

| Column | Type | Description |
|---|---|---|
| trial_id | int32 | Primary key |
| session_id | int32 | FK to sessions |
| trial_number | int16 | 1-based trial index within session |
| start_arm | large_string | Start arm for this trial (proximal or distal to goal) |
| goal_location | large_string | Goal corner for this trial |
| cue_orientation | large_string | Cue monitor position for this trial |
| cue_on | int8 | Whether the landmark was displayed (0/1) |
| time_duration | float | Trial duration in seconds |
| errors | int16 | Rig-logged number of wrong-well entries. `errors == 0` means the first well entered was the reward well; it is not the same as taking the direct route |
| turn_1 | large_string | Rig-logged 1st turn (L/R) at the center intersection, as MazeControl scored it live |
| turn_2 | large_string | Rig-logged 2nd turn (L/R) at the perimeter intersection, as MazeControl scored it live |
| perseveration_score | double | Perseveration score |
| first_error | double | First error indicator |
| goal_zones_visited | large_string | Sequence of goal zones visited |
| correct_route | dictionary<values=string, indices=int8, ordered=0> | Two-turn sequence (LL/LR/RL/RR) for a direct run from start_arm to goal_location. NaN for exposure trials (no defined start_arm/goal). Computed in stage 3b from (start_arm, goal_location) geometry |
| actual_route | dictionary<values=string, indices=int8, ordered=0> | Two-turn sequence actually taken (turn_1 + turn_2). NaN when either turn is missing. Computed in stage 3b from the rig-logged turns, which stay on the table |
| first_choice_latency_s | float | Latency (s) from trial start to the first registered well visit (any well, reward well included); NaN if none. Stage 3c |
| coord_turn_1 | large_string | L/R at 1st choice (center intersection), reconstructed from coordinate trace (stage 3d) |
| coord_turn_2 | large_string | L/R at 2nd choice (perimeter intersection), reconstructed from coordinate trace (stage 3d) |
| coord_route | dictionary<values=string, indices=int8, ordered=0> | Concatenation of coord_turn_1 + coord_turn_2 (stage 3d) |
| turn_2_goal_side | bool | Did the 2nd turn occur at the goal-side perimeter intersection? (stage 3d) |
| goal_side_turn | large_string | L/R at the goal-side perimeter, even after backtracking (stage 3d) |
| second_turn_toward_goal | bool | Did the 2nd turn head toward the goal corner (goal-side or mirrored non-goal-side)? NA when inputs missing (stage 3d) |

### `session_segments.parquet` (559 rows)

| Column | Type | Description |
|---|---|---|
| session_id | int64 | FK to sessions |
| segment_idx | int8 | 0-based index within session (>0 for merged sessions) |
| original_session_id | int64 | Session ID in the raw DB before merging |
| session_number | string | Session number from the original segment |
| date | string | Date of this segment |
| time | string | Time of this segment |
| session_duration | float | Duration of this segment in seconds |
| coordinate_history_file | string | Path on the rig computer of this recording's coordinate CSV |

### `phases.parquet` (43,696 rows)

| Column | Type | Description |
|---|---|---|
| session_id | int32 | FK to sessions |
| phase | dictionary<values=string, indices=int8, ordered=0> | Phase name: "pretrial", "trial", "iti" (training); "presession", "acclimation", "reward" (exposure) |
| phase_idx | int16 | 0-based phase index within session |
| trial_number | int16 | Associated trial number. Training: matches trial_number in trials.parquet. Exposure 1e: 0 (presession), 1-32 (reward). Exposure 2e: 0 (presession), -6 to -1 (acclimation), 1-33 (reward) |
| t_start_ms | int32 | Phase start time in ms from session start |
| t_end_ms | int32 | Phase end time in ms from session start |
| duration_ms | int32 | Phase duration in ms |
| crosses_seam | bool | Whether this phase spans a segment boundary (merged sessions only) |

### `exposure_rewards.parquet` (2,712 rows)

| Column | Type | Description |
|---|---|---|
| session_id | int32 | FK to sessions |
| reward_idx | int16 | 0-based reward index within session |
| cycle | int16 | Reward cycle (all 4 wells visited = 1 cycle) |
| well_zone | int8 | Zone ID of the visited well (1, 5, 17, or 21) |
| well_name | large_string | Well name ("SW", "NW", "SE", "NE") |
| t_entry_ms | double | Reconstructed time of well entry from coordinates (ms) |
| t_logged_ms | double | The rig's `session_event` time (ms): the end of the post-reward pause, not the collection |
| dwell_ms | double | Time spent in well zone (ms) |
| wells_available | large_string | Comma-separated list of wells available at time of entry |

### `trial_well_visits.parquet` (26,484 rows)

| Column | Type | Description |
|---|---|---|
| trial_id | int32 | FK to trials |
| session_id | int32 | FK to sessions |
| visit_idx | int16 | 0-based visit index within trial |
| well_zone | int8 | Zone ID of visited well (1, 5, 17, or 21) |
| well_name | large_string | Well name ("SW", "NW", "SE", "NE") |
| t_entry_ms | int32 | First frame of the run that registered the visit (ms from session start) |
| t_exit_ms | int32 | Frame the run ended, or trial end (ms) |
| dwell_ms | int32 | Time in well = t_exit_ms - t_entry_ms |
| is_reward | bool | True for the terminal reward visit |

### `trial_zone_sequence.parquet` (209,205 rows)

| Column | Type | Description |
|---|---|---|
| trial_id | int32 | FK to trials |
| session_id | int32 | FK to sessions |
| sequence_number | int16 | 0-based run index within the trial |
| zone | int8 | Zone ID (0-21) |
| t_in_ms | int32 | Entry time (ms from session start) |
| t_out_ms | int32 | Exit time = next run's t_in_ms; for the final run, the last frame's timestamp |
| t_total_ms | int32 | Time in zone = t_out_ms - t_in_ms |

