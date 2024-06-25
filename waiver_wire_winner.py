from espn_api.baseball import League
import pandas as pd
import sqlite3
from datetime import date, timedelta
import requests
import numpy as np
import difflib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

#------------ Build the databases for pitchers and hitters ------------------------#

# database will store the following pieces of information to later perform analysis on
# 1. MLBAM ID (for a unique identifier)
# 2. Player Name (for easier visibility in results)
# 3. Team ID (needed to join on a team's schedule to see if the player has a game that day)
# 4. Current Days Rest (number of days since a player played their last game)
# 5. Rest List (list of previous rest days between playing, i.e. 1, 2, 0, 1)
# 6. Last Score (most recent number of fantasy points scored)
# 7. Score List (list of previous points scored, i.e. 13, 10, -1, 7)
# 8. Score Per Inning (mainly for pitchers and short-outing relievers, but normalize their score to an inning)
# 9. Score Per Inning List (list of previous normalized score per inning)

def build_database(conn):
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS pitcher_rest_and_scoring
                (mlbam_id INTEGER PRIMARY KEY, player_name TEXT, team_id INTEGER, cur_days_rest INTEGER, rest_list INTEGER[],
            last_score INTEGER, score_list INTEGER[], score_per_inn REAL, score_per_inn_list REAL[])''')

    c.execute('''CREATE TABLE IF NOT EXISTS batter_rest_and_scoring
                (mlbam_id INTEGER PRIMARY KEY, player_name TEXT, team_id INTEGER, cur_days_rest INTEGER, rest_list INTEGER[],
            last_score INTEGER, score_list INTEGER[], score_per_pa REAL, score_per_pa_list REAL[])''')

    conn.commit()

# --------------------- Get info for that is needed for further calculations from the MLB Stats API -------------#

base_url = "https://statsapi.mlb.com/api"

class MLBStatsAPIClient:
    
    def __init__(self):
        pass
    
    # get game data of a specific game given a game_pk. Returns values in JSON form

    def get_game(self, game_pk, timecode = None, hydrate = None, fields = None):
        request_url = f"{base_url}/v1.1/game/{game_pk}/feed/live"
    
        query_params = {}
    
        if timecode:
            query_params["timecode"] = timecode
            
        if hydrate:
            valid_hydrations = [h for h in hydrate if h in ["credits", "alignment", "flags", "officials", "preState"]]
        
            query_params["hydrate"] = ",".join(valid_hydrations)
        
        if fields:
            query_params["fields"] = ",".join(fields)
        
        response = requests.request(method="GET", url=request_url, params=query_params)
    
        return response.json()
    
    # get the JSON response of games from a specific day (normally used for the previous day)

    def get_games_by_date(self, date):
        request_url = f"{base_url}/v1/schedule/?sportId=1&date={date}"
        
        response = requests.request(method="GET", url=request_url)
    
        return response.json()
    
    # get the list of teams that are playing on a certain day

    def get_team_schedule_by_date(self, date):

        request_url = f"{base_url}/v1/schedule/?sportId=1&scheduleTypes=games&date={date}"
        
        response = requests.request(method="GET", url=request_url)
    
        return response.json()

# ------------- Helper Methods to hit the create MLBStats Client and Grab Relevant Fantasy Information -------- #

# grab the game_pks from the previous day (in theory could be any date)

def get_days_previous_games(client, date):

    previous_day_games_json = client.get_games_by_date(f"{date}")
    previous_day_games = [game['gamePk'] for game in previous_day_games_json['dates'][0]['games']]

    return previous_day_games

# Define the point system for fantasy scoring using the league's rules
# and calculate what a player scored based on their results

def calculate_player_scoring(client, games_to_run):
    
    pitching_point_system = {
        'outs': 1,
        'earnedRuns': -2,
        'wins': 5,
        'losses': -3,
        'saves': 12,
        'blownSaves': -4,
        'strikeOuts': 5,
        'hits': -1,
        'baseOnBalls': -1,
        'shutouts': 50,
        'hitByPitch': -1,
        'wildPitches': -1,
        'balks': -7,
        'pickoffs': 7,
        'completeGames': 50,
        'holds': 7
    }

    batting_point_system = {
        'doubles': 5,
        'triples': 10,
        'homeRuns': 14,
        'baseOnBalls': 1,
        'runs': 2,
        'rbi': 4,
        'stolenBases': 9,
        'strikeOuts': -1,
        'intentionalWalks': 7,
        'hitByPitch': 1,
        'sacBunts': 1,
        'sacFlies': 1,
        'caughtStealing': -2,
        'groundIntoDoublePlay': -1

    }

    # iterate through the games and get the player info

    pitcher_data = []
    batter_data = []

    for game_pk in games_to_run:
        
        game_data = client.get_game(game_pk)

        # Extract the relevant data that will be added to the database

        for team in ['away', 'home']:
            for player_id, player_info in game_data['liveData']['boxscore']['teams'][team]['players'].items():
                player_name = player_info['person']['fullName']
                player_id = player_info['person']['id']

                # if a player doesn't have a team_id, give him an arbitrary one that won't ever come up

                if 'parentTeamId' in player_info:
                    team_id = player_info['parentTeamId']
                else:
                    team_id = 999
                game_batting_stats = player_info['stats']['batting']
                game_pitching_stats = player_info['stats']['pitching']

                # Calculate fantasy scores for pitching

                innings_pitched_game = game_pitching_stats.get('outs', 0) / 3

                if innings_pitched_game > 0:
                    game_pitching_score = sum(game_pitching_stats.get(stat, 0) * pitching_point_system.get(stat, 0) for stat in pitching_point_system)
                    earned_runs_game = game_pitching_stats.get('earnedRuns', 0)
                    if innings_pitched_game >= 6 and earned_runs_game <= 3:
                        game_pitching_score += 8
                    game_pitching_score_per_inning = round(game_pitching_score / innings_pitched_game, 1)

                    pitcher_data.append({
                        'Player Name': player_name,
                        'Player ID': player_id,
                        'Team ID': team_id,
                        'Game Pitching Fantasy Score': game_pitching_score,
                        'Game Pitching Fantasy Score per Inning': game_pitching_score_per_inning,
                    })

                # Calculate fantasy scores for batting

                plate_appearances = game_batting_stats.get('plateAppearances', 0)
                hits = game_batting_stats.get('hits', 0)

                if plate_appearances > 0:
                    game_batting_score = sum(game_batting_stats.get(stat, 0) * batting_point_system.get(stat, 0) for stat in batting_point_system)
                    if hits > 0:
                        singles = hits - game_batting_stats.get('doubles', 0) - game_batting_stats.get('triples', 0) - game_batting_stats.get('homeRuns', 0)
                        game_batting_score += (singles * 2)
                    game_batting_score_per_pa = round(game_batting_score / plate_appearances, 1)\

                    batter_data.append({
                        'Player Name': player_name,
                        'Player ID': player_id,
                        'Team ID': team_id,
                        'Game Batting Fantasy Score': game_batting_score,
                        'Game Batting Fantasy Score per PA': game_batting_score_per_pa,
                    })

    # Create DataFrames of the player data which will later be written back to the database
    pitcher_df = pd.DataFrame(pitcher_data)
    batter_df = pd.DataFrame(batter_data)

    return pitcher_df, batter_df

# method to write back the updated dataframes to the database to be used in the future

def update_player_data(conn, pitcher_df, batter_df):

    # Connect to the SQLite database

    c = conn.cursor()

    # Fetch the existing data from the database

    c.execute("SELECT * FROM pitcher_rest_and_scoring")
    existing_pitchers = pd.DataFrame(c.fetchall(), columns=['mlbam_id', 'player_name', 'team_id', 'cur_days_rest', 'rest_list', 'last_score', 'score_list', 'score_per_inn', 'score_per_inn_list'])

    c.execute("SELECT * FROM batter_rest_and_scoring")
    existing_batters = pd.DataFrame(c.fetchall(), columns=['mlbam_id', 'player_name', 'team_id', 'cur_days_rest', 'rest_list', 'last_score', 'score_list', 'score_per_pa', 'score_per_pa_list'])

    # Update the data for pitchers who played today

    for _, row in pitcher_df.iterrows():
        player_name = row['Player Name']
        player_id = row['Player ID']
        team_id = row['Team ID']
        game_pitching_score = row['Game Pitching Fantasy Score']
        game_pitching_score_per_inning = row['Game Pitching Fantasy Score per Inning']

        if player_name in existing_pitchers['player_name'].values:
            player_data = existing_pitchers.loc[existing_pitchers['player_name'] == player_name]
            
            # Update rest_list and cur_days_rest

            rest_list = player_data['rest_list'].values[0]
            if rest_list is None:
                rest_list = []
            else:
                rest_list = eval(rest_list)
            rest_list.append(player_data['cur_days_rest'].values[0])  # Add the previous rest days to the list
            existing_pitchers.loc[existing_pitchers['player_name'] == player_name, 'rest_list'] = str(rest_list)
            existing_pitchers.loc[existing_pitchers['player_name'] == player_name, 'cur_days_rest'] = 0

            # Update score_list and last_score

            score_list = player_data['score_list'].values[0]
            if score_list is None:
                score_list = []
            else:
                score_list = eval(score_list)
            score_list.append(player_data['last_score'].values[0])  # Add the previous score to the list
            existing_pitchers.loc[existing_pitchers['player_name'] == player_name, 'score_list'] = str(score_list)
            existing_pitchers.loc[existing_pitchers['player_name'] == player_name, 'last_score'] = game_pitching_score

            # Update score_per_inn_list and score_per_inn

            score_per_inn_list = player_data['score_per_inn_list'].values[0]
            if score_per_inn_list is None:
                score_per_inn_list = []
            else:
                score_per_inn_list = eval(score_per_inn_list)
            score_per_inn_list.append(player_data['score_per_inn'].values[0])  # Add the previous score per inning to the list
            existing_pitchers.loc[existing_pitchers['player_name'] == player_name, 'score_per_inn_list'] = str(score_per_inn_list)
            existing_pitchers.loc[existing_pitchers['player_name'] == player_name, 'score_per_inn'] = game_pitching_score_per_inning

        else:
            new_row = {'mlbam_id': player_id, 'player_name': player_name, 'team_id': team_id, 'cur_days_rest': 0, 'rest_list': str([]), 'last_score': game_pitching_score, 'score_list': str([]), 'score_per_inn': game_pitching_score_per_inning, 'score_per_inn_list': str([])}
            existing_pitchers = pd.concat([existing_pitchers, pd.DataFrame([new_row])], ignore_index=True)

    # Update the data for batters who played today

    for _, row in batter_df.iterrows():
        player_name = row['Player Name']
        player_id = row['Player ID']
        team_id = row['Team ID']
        game_batting_score = row['Game Batting Fantasy Score']
        game_batting_score_per_pa = row['Game Batting Fantasy Score per PA']

        if player_name in existing_batters['player_name'].values:
            player_data = existing_batters.loc[existing_batters['player_name'] == player_name]
            
            # Update rest_list and cur_days_rest

            rest_list = player_data['rest_list'].values[0]
            if rest_list is None:
                rest_list = []
            else:
                rest_list = eval(rest_list)
            rest_list.append(player_data['cur_days_rest'].values[0])  # Add the previous rest days to the list
            existing_batters.loc[existing_batters['player_name'] == player_name, 'rest_list'] = str(rest_list)
            existing_batters.loc[existing_batters['player_name'] == player_name, 'cur_days_rest'] = 0

            # Update score_list and last_score

            score_list = player_data['score_list'].values[0]
            if score_list is None:
                score_list = []
            else:
                score_list = eval(score_list)
            score_list.append(player_data['last_score'].values[0])  # Add the previous score to the list
            existing_batters.loc[existing_batters['player_name'] == player_name, 'score_list'] = str(score_list)
            existing_batters.loc[existing_batters['player_name'] == player_name, 'last_score'] = game_batting_score

            # Update score_per_pa_list and score_per_pa

            score_per_pa_list = player_data['score_per_pa_list'].values[0]
            if score_per_pa_list is None:
                score_per_pa_list = []
            else:
                score_per_pa_list = eval(score_per_pa_list)
            score_per_pa_list.append(player_data['score_per_pa'].values[0])  # Add the previous score per PA to the list
            existing_batters.loc[existing_batters['player_name'] == player_name, 'score_per_pa_list'] = str(score_per_pa_list)
            existing_batters.loc[existing_batters['player_name'] == player_name, 'score_per_pa'] = game_batting_score_per_pa

        else:
            new_row = {'mlbam_id': player_id, 'player_name': player_name, 'team_id': team_id, 'cur_days_rest': 0, 'rest_list': str([]), 'last_score': game_batting_score, 'score_list': str([]), 'score_per_pa': game_batting_score_per_pa, 'score_per_pa_list': str([])}
            existing_batters = pd.concat([existing_batters, pd.DataFrame([new_row])], ignore_index=True)

    # Update the current_days_rest for players who didn't play today

    existing_pitchers.loc[~existing_pitchers['player_name'].isin(pitcher_df['Player Name']), 'cur_days_rest'] += 1
    existing_batters.loc[~existing_batters['player_name'].isin(batter_df['Player Name']), 'cur_days_rest'] += 1

    # Write the updated data back to the database

    existing_pitchers.to_sql('pitcher_rest_and_scoring', conn, if_exists='replace', index=False)
    existing_batters.to_sql('batter_rest_and_scoring', conn, if_exists='replace', index=False)

    # Commit the changes

    conn.commit()

# basic calculation borrowed from economics. Higher sharpe ratio
# indicated higher average points with low variance

def calculate_fantasy_sharpe_ratio(row, score_list_col):
    score_list = eval(row[score_list_col])
    if score_list:
        # Check if the list is not empty
        if len(score_list) > 0:
            score_mean = np.mean(score_list)
            score_std = np.std(score_list)
            if score_std > 0:
                return score_mean / score_std
            else:
                return 0  # Return 0 if the standard deviation is 0
        else:
            # Handle the case where the list is empty
            return 0
    else:
        return 0  # Return 0 if the score_list is empty

# calculate which players are the most likely to play based on how many rest days they have had compared to their median rest
# rank these players by their Sharpe ratio (which ones will likely get the most stable return of points if they pitch)

def predict_players(conn):

    # Connect to the SQLite database
    c = conn.cursor()

    # Fetch the data from the database
    c.execute("SELECT * FROM pitcher_rest_and_scoring")
    pitchers = pd.DataFrame(c.fetchall(), columns=['mlbam_id', 'player_name', 'team_id', 'cur_days_rest', 'rest_list', 'last_score', 'score_list', 'score_per_inn', 'score_per_inn_list'])

    c.execute("SELECT * FROM batter_rest_and_scoring")
    batters = pd.DataFrame(c.fetchall(), columns=['mlbam_id', 'player_name', 'team_id', 'cur_days_rest', 'rest_list', 'last_score', 'score_list', 'score_per_pa', 'score_per_pa_list'])

    # Calculate the fantasy_sharpe_ratio for pitchers
    pitchers['fantasy_sharpe_ratio'] = pitchers.apply(lambda row: calculate_fantasy_sharpe_ratio(row, 'score_per_inn_list'), axis=1)

    # Filter pitchers who are likely to play
    likely_pitchers = pitchers[pitchers['cur_days_rest'] >= pitchers['rest_list'].apply(lambda x: np.median(eval(x)) if x else 0)]

    # Rank likely pitchers by fantasy_sharpe_ratio
    ranked_pitchers = likely_pitchers.sort_values(by='fantasy_sharpe_ratio', ascending=False)

    # Calculate the fantasy_sharpe_ratio for batters
    batters['fantasy_sharpe_ratio'] = batters.apply(lambda row: calculate_fantasy_sharpe_ratio(row, 'score_per_pa_list'), axis=1)

    # Filter batters who are likely to play
    likely_batters = batters[batters['cur_days_rest'] >= batters['rest_list'].apply(lambda x: np.median(eval(x)) if x else 0)]

    # Rank likely batters by fantasy_sharpe_ratio
    ranked_batters = likely_batters.sort_values(by='fantasy_sharpe_ratio', ascending=False)

    return ranked_pitchers, ranked_batters

# method for recapping what happened the previous day
# display the top pitchers and hitters

def get_top_players(conn):
    # Connect to the SQLite database
    c = conn.cursor()

    # Fetch the data from the database
    c.execute("SELECT * FROM pitcher_rest_and_scoring")
    pitchers = pd.DataFrame(c.fetchall(), columns=['mlbam_id', 'player_name', 'team_id', 'cur_days_rest', 'rest_list', 'last_score', 'score_list', 'score_per_inn', 'score_per_inn_list'])

    c.execute("SELECT * FROM batter_rest_and_scoring")
    batters = pd.DataFrame(c.fetchall(), columns=['mlbam_id', 'player_name', 'team_id', 'cur_days_rest', 'rest_list', 'last_score', 'score_list', 'score_per_pa', 'score_per_pa_list'])

    # Get top 5 pitchers by score_per_inn
    top_pitchers_by_score_per_inn = pitchers.nlargest(5, 'score_per_inn')[['player_name', 'score_per_inn']]

    # Get top 5 pitchers by last_score
    top_pitchers_by_last_score = pitchers.nlargest(5, 'last_score')[['player_name', 'last_score']]

    # Get top 5 batters by score_per_pa
    top_batters_by_score_per_pa = batters.nlargest(5, 'score_per_pa')[['player_name', 'score_per_pa']]

    # Get top 5 batters by last_score
    top_batters_by_last_score = batters.nlargest(5, 'last_score')[['player_name', 'last_score']]

    return top_pitchers_by_score_per_inn, top_pitchers_by_last_score, top_batters_by_score_per_pa, top_batters_by_last_score

# join all of the players with the list of players who are available in the fantasy league
# returns results with the players who are available to be acquired

def join_with_waiver_players(league, probable_pitchers, probable_batters, top_score_per_inn_p, top_score_p, top_score_per_pa_b, top_score_b):
    
    def find_closest_match(name, free_agents_available):
        closest_match = difflib.get_close_matches(name, free_agents_available, n=1, cutoff=0.95)
        return closest_match[0] if closest_match else None
    
    # get list of available free agents
    free_agents = league.free_agents(size = 1500)
    free_agents_available = [player.name for player in free_agents]

    # Filter ranked pitchers
    probable_pitchers['player_name'] = probable_pitchers['player_name'].apply(lambda x: find_closest_match(x, free_agents_available))
    probable_pitchers = probable_pitchers[probable_pitchers['player_name'].notnull()]

    # Filter ranked batters
    probable_batters['player_name'] = probable_batters['player_name'].apply(lambda x: find_closest_match(x, free_agents_available))
    probable_batters = probable_batters[probable_batters['player_name'].notnull()]

    # Filter top pitchers by score_per_inn
    top_score_per_inn_p['player_name'] = top_score_per_inn_p['player_name'].apply(lambda x: find_closest_match(x, free_agents_available))
    top_score_per_inn_p = top_score_per_inn_p[top_score_per_inn_p['player_name'].notnull()]

    # Filter top pitchers by last_score
    top_score_p['player_name'] = top_score_p['player_name'].apply(lambda x: find_closest_match(x, free_agents_available))
    top_score_p = top_score_p[top_score_p['player_name'].notnull()]

    # Filter top batters by score_per_pa
    top_score_per_pa_b['player_name'] = top_score_per_pa_b['player_name'].apply(lambda x: find_closest_match(x, free_agents_available))
    top_score_per_pa_b = top_score_per_pa_b[top_score_per_pa_b['player_name'].notnull()]

    # Filter top batters by last_score
    top_score_b['player_name'] = top_score_b['player_name'].apply(lambda x: find_closest_match(x, free_agents_available))
    top_score_b = top_score_b[top_score_b['player_name'].notnull()]

    return probable_pitchers, probable_batters, top_score_per_inn_p, top_score_p, top_score_per_pa_b, top_score_b

# join the available players with the games that are happening
# i.e. a player can't get points if their team doesn't play

def join_with_todays_games(client, date, probable_pitchers, probable_batters):
    teams_today_json = client.get_team_schedule_by_date(date)
    
    teams_today = []
    for game in teams_today_json['dates'][0]['games']:
        away_team_id = game['teams']['away']['team']['id']
        home_team_id = game['teams']['home']['team']['id']
        teams_today.append(away_team_id)
        teams_today.append(home_team_id)

    probable_pitchers = probable_pitchers[probable_pitchers['team_id'].isin(teams_today)]
    probable_batters = probable_batters[probable_batters['team_id'].isin(teams_today)]

    return probable_pitchers, probable_batters

# send yourself or others an automated email with the results so that you can quickly identify players for pick-up

def send_email(date, prob_pitchers_available, prob_batters_available, top_score_per_inn_p_available, top_score_p_available, top_score_per_pa_b_available, top_score_b_available):
    # Your Gmail account credentials
    sender_email = {SENDER_EMAIL}
    receiver_email = {RECEIVER_EMAIL}
    password = {EMAIL_PASSKEY}

    # Create a multipart message
    message = MIMEMultipart("alternative")
    message["Subject"] = f"Waiver Wire Adds - {date}"
    message["From"] = sender_email
    message["To"] = receiver_email

    # Construct the email body with the DataFrames
    html_body = f"<h3>Probable Pitchers:</h3>{prob_pitchers_available.to_html(index=False)}<br><br>"
    html_body += f"<h3>Probable Batters:</h3>{prob_batters_available.to_html(index=False)}<br><br>"
    html_body += f"<h3>Top Pitchers by Score per Inning:</h3>{top_score_per_inn_p_available.to_html(index=False)}<br><br>"
    html_body += f"<h3>Top Pitchers by Last Score:</h3>{top_score_p_available.to_html(index=False)}<br><br>"
    html_body += f"<h3>Top Batters by Score per Plate Appearance:</h3>{top_score_per_pa_b_available.to_html(index=False)}<br><br>"
    html_body += f"<h3>Top Batters by Last Score:</h3>{top_score_b_available.to_html(index=False)}"

    # Attach the email body to the message
    message.attach(MIMEText(html_body, "html"))

    # Create a secure SMTP session
    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()

    try:
        # Login to your Gmail account
        server.login(sender_email, password)
        # Send the email
        server.sendmail(sender_email, receiver_email, message.as_string())
    except Exception as e:
        print(f"Error occurred while sending email: {e}")
    finally:
        # Close the SMTP server connection
        server.quit()


# Get yesterday's date in MM/DD/YYYY format
yesterday = (date.today() - timedelta(days=1)).strftime("%m/%d/%Y")
today = (date.today()).strftime("%m/%d/%Y")

# connect to the DB
conn = sqlite3.connect('/home/aj/code_scripts/player_rest_and_scoring.db')
build_database(conn)

# initialize the instance of your ESPN fantasy league
client = MLBStatsAPIClient()
league = League(league_id={YOUR_LEAGUE_ID}, year=2024, espn_s2={YOUR_ESPN_S2}, swid={YOUR_ESPN_SWID})

# grab yesterdays games and perform the calculations
yesterdays_games = get_days_previous_games(client, yesterday)

pitcher_df, batter_df = calculate_player_scoring(client, yesterdays_games)
update_player_data(conn, pitcher_df, batter_df)
probable_pitchers, probable_batters = predict_players(conn)
top_score_per_inn_p, top_score_p, top_score_per_pa_b, top_score_b = get_top_players(conn)

# get the most likely players to player and send out the email
prob_pitchers_available, prob_batters_available, top_score_per_inn_p_available, top_score_p_available, top_score_per_pa_b_available, top_score_b_available = join_with_waiver_players(league, probable_pitchers, probable_batters, top_score_per_inn_p, top_score_p, top_score_per_pa_b, top_score_b)
prob_pitchers_today, prob_batters_today = join_with_todays_games(client, today, prob_pitchers_available, prob_batters_available)

send_email(today, prob_pitchers_today, prob_batters_today, top_score_per_inn_p_available, top_score_p_available, top_score_per_pa_b_available, top_score_b_available)

conn.close()