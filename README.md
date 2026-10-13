# GameShelf

GameShelf is a web-based video game discovery, cataloging, and personal tracking platform inspired by services such as IMDb, Rotten Tomatoes, and MyAnimeList. It combines game-list tracking with algorithmic recommendations, community ratings and reviews, and moderation tools.

## Project Overview

GameShelf provides a centralized, platform-agnostic space where users can:

- Discover and search for video games
- Filter games by genre, platform, and release year
- Maintain a personal game library
- Track games by playthrough status
- Rate and review games
- Receive personalized recommendations
- Explore similar titles
- View trending games
- Interact with community ratings and reviews

The system uses a decoupled architecture consisting of a **React frontend**, **Django REST Framework backend**, and **PostgreSQL database**.

## Problem Being Solved

GameShelf is designed to address several common problems in game discovery and tracking:

1. **Fragmented Game Tracking** — Players often use multiple platforms and services, making it difficult to maintain one organized game library.
2. **Rating Distortions** — Games with only a few extreme ratings can unfairly appear above titles with thousands of ratings.
3. **Generic Recommendations** — Traditional storefront recommendations may not sufficiently reflect an individual user's preferences.
4. **Review-Bombing and Rating Manipulation** — Coordinated rating manipulation and spam reviews can affect community scores.
5. **Lack of Recommendation Context** — Users are often given recommendations without an explanation of why a game was suggested.

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React |
| Backend | Django REST Framework |
| Database | PostgreSQL |
| Recommendation / ML | SVD, TF-IDF, Cosine Similarity, K-Means, PCA, Isolation Forest |
| Security | HTTPS/TLS, CSRF, RBAC, rate limiting, secure cookies |

## Core Features

### User Accounts and Profiles

- Secure registration and login
- Email verification
- Logout
- Password reset and password modification
- Custom avatars
- User biographies
- Favorite game highlights
- Personal rating history
- Written review archives
- Public/private personal game lists

### Game Catalog, Search, and Filtering

- Search games by title
- Filter by:
  - Genre
  - Platform
  - Release Year
- Sort by Bayesian overall rating
- Sort by popularity/activity
- Detailed game pages containing:
  - Game metadata
  - Cover art
  - Screenshots
  - Community scores
  - Rating counts
  - User reviews
  - Similar game recommendations

### Personal Game Tracking

Users can organize games using five tracking statuses:

- **Planning to Play**
- **Playing**
- **Completed**
- **On Hold**
- **Dropped**

Users can also:

- Add or remove games
- Change playthrough status
- Like games
- Give personal ratings from 1–10

### Community Ratings and Reviews

- 1–10 rating scale
- One active rating per user per game
- Rating editing and deletion
- Detailed written reviews
- Spoiler tags
- User reporting
- Community moderation workflow

## Recommendation and Algorithmic Intelligence

GameShelf uses several algorithms to improve discovery and personalization.

### Bayesian Weighted Rating

A Bayesian weighted rating is used to produce a fairer community score by considering both the average rating and the number of ratings.

This helps prevent games with only a small number of extreme ratings from dominating global rankings.

### Matrix Factorization / SVD

**Singular Value Decomposition (SVD)** is used for collaborative filtering.

It analyzes user-game interactions to predict games that a user may enjoy even when the user has not rated those games yet.

**Purpose:** `Recommended for You`

### TF-IDF + Cosine Similarity

Game metadata such as genres, tags, descriptions, and features are converted into TF-IDF vectors.

Cosine similarity is then used to identify games with similar content.

**Purpose:** `Similar Titles`

### K-Means + PCA

K-Means clustering is used to group games and users into preference segments, such as:

- Indie / Metroidvania
- RPG / Open-World

PCA can optionally be used for dimensionality reduction and visualization.

**Purpose:** `Taste-Based Discovery`

### Time-Series Moving Averages

Moving averages are used to monitor short-term changes in:

- Ratings
- Reviews
- Likes
- List additions

This allows GameShelf to identify games that are currently gaining activity.

**Purpose:** `Trending Games`

## Recommendation Rules

GameShelf applies additional business rules to make recommendations more useful.

### Filtering

Recommendations automatically exclude:

- Completed games
- Games marked as Not Interested
- Games already in the user's active list

### Personal Preference Weighting

Higher importance is given to:

- Personal ratings of **8–10**
- Explicitly liked games

Unplayed list entries receive less weight.

### Recommendation Explanations

Recommendations include human-readable reasons, such as:

> "Because you completed Hollow Knight"

or:

> "Top recommendation in Action/RPG"

Recommendations are displayed on the user's home page and game details pages.

## Moderation and Administration

GameShelf includes an administrative and moderation dashboard for managing community activity.

Administrators and moderators can:

- Review user-reported content
- Review automatically flagged reviews and ratings
- Inspect anomaly detection alerts
- Edit game catalog metadata
- Dismiss false flags
- Take action against abusive accounts

## Security

### Input Validation and Injection Defense

- Backend input sanitization
- Strict type validation
- Parameterized database queries
- HTML sanitization and escaping
- XSS protection

### Authentication and Session Security

- Password hashing using bcrypt or Argon2
- Plain-text passwords are not stored
- Secure cookies using:
  - HttpOnly
  - SameSite
  - Secure flags
- Rate-limited login endpoints
- Secure token-based password reset

### CSRF and Authorization

- CSRF verification on state-changing endpoints
- Server-side Role-Based Access Control (RBAC)
- Authorization checks on API endpoints
- Users cannot modify other users' profiles, ratings, or administrative settings through client-side manipulation

### API and Network Security

- IP-based and account-based API rate limiting
- HTTPS/TLS for client-server communication
- Strict CORS policy
- Security headers including:
  - Content-Security-Policy
  - X-Frame-Options
  - X-Content-Type-Options
- Database credentials and private API keys stored through environment variables
- No secrets stored directly in source code

## Review-Bombing and Anomaly Detection

GameShelf uses an **Isolation Forest** machine learning model to identify potentially abnormal user behavior.

The system can detect patterns such as:

- Sudden spikes in rating activity for a game
- Large concentrations of extreme 1/10 or 10/10 ratings within short periods
- Suspicious script-like submission patterns

Detected activities are sent to the moderation dashboard for **human review** rather than being automatically deleted.

## Target Users

| User Type | Benefits |
|---|---|
| Casual Players | Easy discovery of top-rated and trending games |
| Regular Gamers | Backlog management, status tracking, and rating history |
| Discovery Seekers | Personalized recommendations |
| Reviewers & Community | Ratings, reviews, spoiler protection, and reporting |
| Administrators & Moderators | Moderation, anomaly detection, and catalog management |
| Unauthenticated Visitors | Public game search, filtering, and exploration |

## Requirements Gathering

The project requirements were developed using:

- Competitive benchmarking and analysis
- Persona mapping and user profiling
- User journey mapping
- Algorithmic feasibility modeling
- Prototyping and iterative structural analysis

The project considered six core user classes:

- Casual Players
- Regular Gamers
- Discovery Seekers
- Reviewers
- Moderators
- Unauthenticated Visitors

## Project Team

- Marvin B. Tomales
- Lars Ulrich Bernardez
- Clark Kent Raguhos
- Miguel Achurra

## Project Status

GameShelf is a project focused on combining traditional video game tracking with personalized recommendation algorithms and security-focused community moderation.

## License

No license information was specified in the project document.
