suppressPackageStartupMessages({
  library(baseballr)
  library(DBI)
  library(RSQLite)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
db_path <- ifelse(length(args) >= 1, args[[1]], "../data/mlb_draft_2026.db")
year <- ifelse(length(args) >= 2, as.integer(args[[2]]), 2026)

con <- dbConnect(SQLite(), db_path)
on.exit(dbDisconnect(con), add = TRUE)

x <- mlb_draft_prospects(year = year)
drafted <- subset(x, !is.na(pick_number) & is_drafted == TRUE)

for (i in seq_len(nrow(drafted))) {
  row <- drafted[i,]
  sql <- "
  INSERT INTO actual_picks (
      draft_year, pick_number, round_label, team_id, team_name, team_abbrev,
      mlb_person_id, player_name, player_position, school_name, source,
      source_event_id, raw_payload, updated_at
  ) VALUES (
      :draft_year, :pick_number, :round_label, :team_id, :team_name, :team_abbrev,
      :mlb_person_id, :player_name, :player_position, :school_name, :source,
      :source_event_id, :raw_payload, CURRENT_TIMESTAMP
  )
  ON CONFLICT(draft_year, pick_number) DO UPDATE SET
      team_id=excluded.team_id,
      team_name=excluded.team_name,
      team_abbrev=excluded.team_abbrev,
      mlb_person_id=excluded.mlb_person_id,
      player_name=excluded.player_name,
      player_position=excluded.player_position,
      school_name=excluded.school_name,
      raw_payload=excluded.raw_payload,
      updated_at=CURRENT_TIMESTAMP
  "

  params <- list(
    draft_year = year,
    pick_number = row$pick_number,
    round_label = row$pick_round,
    team_id = row$team_id,
    team_name = row$team_name,
    team_abbrev = row$team_abbreviation,
    mlb_person_id = row$person_id,
    player_name = row$person_full_name,
    player_position = row$person_primary_position_name,
    school_name = row$school_name,
    source = 'baseballr_mlb_draft_prospects',
    source_event_id = paste(year, row$pick_number, row$person_id, sep=':'),
    raw_payload = toJSON(as.list(row), auto_unbox = TRUE, null = 'null', na = 'null')
  )

  dbExecute(con, sql, params = params)
}

cat(sprintf("Upserted %s actual picks for %s\n", nrow(drafted), year))
