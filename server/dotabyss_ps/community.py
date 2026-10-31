from __future__ import annotations

import json

from .gameplay_catalog import GameplayError, number


class Community:
    def __init__(self, connection, revision, user_id):
        self.connection, self.revision, self.user_id = connection, revision, user_id

    def public_profile(self, identifier):
        row = self.connection.execute(
            "SELECT g.contents,g.created_at,p.last_login_at FROM game_states g LEFT JOIN profile_presence p ON p.profile_id=g.profile_id WHERE g.profile_id=? AND g.master_revision=?",
            (number(identifier), self.revision)).fetchone()
        if row is None:
            raise GameplayError("Local player profile was not found")
        try:
            records = json.loads(row["contents"])["records"]
            status, profile = records["t_statuses"]["up"][0], records["t_profiles"]["up"][0]
            return {"t_status": {"user_id": identifier, "name": status["name"], "user_rank": status["user_rank"],
                                 "last_login_at": row["last_login_at"] or row["created_at"]},
                    "t_profile": {"user_id": identifier, **{k: profile[k] for k in ("favorite_m_character_skin_id", "m_degree_id", "comment")}}}
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise GameplayError("Stored public profile is invalid") from error

    def related_ids(self, *, followers=False):
        subject, target = ("followed_id", "follower_id") if followers else ("follower_id", "followed_id")
        return [r[0] for r in self.connection.execute(
            f"SELECT f.{target} FROM follows f JOIN game_states g ON g.profile_id=f.{target} WHERE f.{subject}=? AND g.master_revision=? ORDER BY f.{target}",
            (self.user_id, self.revision))]

    def counts(self):
        return {"follow_count": len(self.related_ids()), "follower_count": len(self.related_ids(followers=True))}

    def lists(self):
        return {"follow_list": [self.public_profile(i) for i in self.related_ids()],
                "follower_list": [self.public_profile(i) for i in self.related_ids(followers=True)]}

    def recommendations(self, limit):
        candidates = self.connection.execute(
            "SELECT g.profile_id FROM game_states g WHERE g.master_revision=? AND g.profile_id<>? AND NOT EXISTS(SELECT 1 FROM follows f WHERE f.follower_id=? AND f.followed_id=g.profile_id) ORDER BY g.profile_id LIMIT ?",
            (self.revision, self.user_id, self.user_id, limit))
        return [self.public_profile(r[0]) for r in candidates]

    def follow(self, identifier, limit):
        self.public_profile(identifier)
        if identifier == self.user_id:
            raise GameplayError("A player cannot follow themselves")
        existing = self.connection.execute("SELECT 1 FROM follows WHERE follower_id=? AND followed_id=?", (self.user_id, identifier)).fetchone()
        if existing is None:
            count = self.connection.execute("SELECT COUNT(*) FROM follows WHERE follower_id=?", (self.user_id,)).fetchone()[0]
            if count >= limit:
                raise GameplayError("Local follow limit reached")
            self.connection.execute("INSERT INTO follows(follower_id,followed_id) VALUES (?,?)", (self.user_id, identifier))

    def unfollow(self, identifier):
        self.connection.execute("DELETE FROM follows WHERE follower_id=? AND followed_id=?", (self.user_id, number(identifier)))

    def remove_follower(self, identifier):
        self.connection.execute("DELETE FROM follows WHERE follower_id=? AND followed_id=?", (number(identifier), self.user_id))
