from app import app
from models import Player, Title

with app.app_context():
    p = Player.query.get(1)
    print('player:', p)
    if p is not None:
        stats = p.calculate_stats()
        print('top1_count in stats:', 'top1_count' in stats, stats.get('top1_count'))
        t = Title.query.first()
        print('title:', t)
        if t:
            try:
                print('check_unlocked:', t.check_unlocked(p))
            except Exception as e:
                print('check_unlocked raised', type(e).__name__, e)
