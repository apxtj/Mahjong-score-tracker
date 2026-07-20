#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
テストデータをデータベースに追加するスクリプト
"""
import os
import sys
from app import app, db
from models import Player, Game
from datetime import datetime, timedelta

def seed_test_data():
    """テストプレイヤーとゲームを追加"""
    with app.app_context():
        # 既存のプレイヤーをチェック
        existing_players = Player.query.all()
        if existing_players:
            print(f"既存プレイヤー数: {len(existing_players)}")
            for player in existing_players:
                print(f"  - {player.name} (ID: {player.id})")
            print("\nゲームをいくつか追加してテストします...\n")

            # テストゲームを追加（20ゲーム）
            base_date = datetime.now() - timedelta(days=30)
            for i in range(20):
                game = Game(
                    date=base_date + timedelta(days=i),
                    player1_id=existing_players[0].id,
                    player2_id=existing_players[1].id if len(existing_players) > 1 else existing_players[0].id,
                    player3_id=existing_players[2].id if len(existing_players) > 2 else existing_players[0].id,
                    player4_id=existing_players[3].id if len(existing_players) > 3 else existing_players[0].id,
                    score1=30000 + (i % 10) * 1000,
                    score2=25000 - (i % 10) * 500,
                    score3=20000 + (i % 10) * 300,
                    score4=25000 - (i % 10) * 800,
                    oka=0,
                    uma1=1500,
                    uma2=500,
                    uma3=-500,
                    uma4=-1500
                )
                db.session.add(game)

            db.session.commit()
            print(f"✓ 20件のテストゲームを追加しました。")
        else:
            print("プレイヤーが存在しません。プレイヤーを先に作成してください。")

if __name__ == '__main__':
    seed_test_data()
