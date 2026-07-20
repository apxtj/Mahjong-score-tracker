#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
称号をデータベースに追加・更新するスクリプト。

バッジ画像はすべて PNG を使用します。
このスクリプトは画像ファイル自体は生成しません。
"""

from app import app, db
from models import Title


def seed_titles():
    """称号をDBに追加・更新する"""
    with app.app_context():
        titles = [
            {
                "name": "トップチャンピオン",
                "badge_filename": "top_champion.png",
                "required_total_games": 1,
                "required_avg_rank": 4.1,
                "required_win_count": 0,
                "required_last_avoidance_rate": 0.0,
                "required_highest_rank_1_rate": True,
            },
            {
                "name": "ラス回避王",
                "badge_filename": "last_dodging.png",
                "required_total_games": 1,
                "required_avg_rank": 4.1,
                "required_win_count": 0,
                "required_last_avoidance_rate": 0.0,
                "required_highest_last_avoidance_rate": True,
            },
            {
                "name": "荒波の覇者",
                "badge_filename": "standard_deviation.png",
                "required_total_games": 1,
                "required_avg_rank": 4.1,
                "required_win_count": 0,
                "required_last_avoidance_rate": 0.0,
                "required_highest_raw_score_std_dev": True,
            },
        ]

        added_count = 0
        updated_count = 0
        for title_data in titles:
            existing_title = Title.query.filter_by(name=title_data["name"]).first()
            if existing_title:
                for key, value in title_data.items():
                    setattr(existing_title, key, value)
                updated_count += 1
            else:
                db.session.add(Title(**title_data))
                added_count += 1

        db.session.commit()
        print(f"added: {added_count} updated: {updated_count}")


if __name__ == "__main__":
    seed_titles()
