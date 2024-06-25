# Waiver Wire Winner
Automated ESPN Fantasy Baseball Assistant Manager

## Motivation

I love fantasy baseball. However, being in competitive 20-team, daily league, I don't have the time to patrol the waiver wire for hours to find relievers who will likely pitch or batters who will likely start. This assistant is an initial attempt to do the hard work of analysis for me and identify the players who are most likely to play and score points.

## General Concept

The general concept of this automated assistant is to quickly perform all of the mental calculations that I normally would when evaluating a player to pick up. For example, when picking up a reliever, I look at when the last time they have pitched, what the distribution of their scores look like, and what team that they are playing against that day. Based on this information, I pick up the reliever who is most likely to pitch and score the most ("risk-free") points if they do pitch. To replicate this analysis, the assistant uses a player's rest days and score information to algorithmically determine who is the best pick-up, searching through all of the eligible free agents in the league (something that would be nearly impossible for me to do).

## Process Workflow

1. Grab all of the game data from the previous day using the MLB Stats API
2. Parse out the performances for each individual pitcher and batter and calculate the fantasy point impact
3. Update (or add to) the database to reflect the new rest and scoring information
4. Identify the most likely players to play and how they will likely score
5. Filter these likely players to only reflect the players who are available free agents in your fantasy league using the ESPN API
6. Filter these players to only the players who have games on the current day
7. Send an email to yourself with the players, ranked by best choices, and including valuable insights

## Algorithm

The algorithm to identify players is two-fold. The first aspect deals with rest days. The assistant will keep track of how many rest days it has been since a player has played. This eventually turns into a list that has the distribution of rest days between playing. Currently, the assistant will calculate the median rest days between game appearances (to smooth outliers) and compare that number to the current number of rest days a player has had. If a player's current number of rest days is greater than their median number of rest days between appearances, they are more likely to pitch/play. The second aspect deals with how to rank these likely players. To do so, an equation from economics called the Sharpe Ratio is adopted. A Sharpe Ratio in economics compares an investment's return to its risk; the higher the ratio means that the investment is likely to do well with very little risk or variance in those good results. For a fantasy player, we ideally want the same - a player who will score a high number of points without the risk of scoring poorly. To calculate the ratio, the average and standard deviation of the player's previous scores are taken and entered into the formula: mean_score / std_of_scores. Then, the higher the ratio, the better the free agent candidate.

## Future Improvements
1. Simplify the workflow and code so there is less overhead and calls to the database
2. Identify players who are likely to have a "boom" performance rather than just a "high Sharpe ratio ceiling"
3. Integrate matchup information (i.e. Mariners strike out a lot)
4. Integrate IL and call-up information
5. Leverage more of the capabilities that exist within the ESPN API

Note: ESPN has been changing around its APIs and permissions recently so some of the webhooks may no longer work

