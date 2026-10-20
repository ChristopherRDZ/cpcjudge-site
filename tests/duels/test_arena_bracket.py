"""Bracket generation: seeding order, byes, links between rounds and the third place match."""
from django.test import SimpleTestCase, TestCase

from arena_testutil import organizer, player
from judge.arena import tournament as arena_tournament
from judge.models.arena import ArenaMatch, ArenaTournament


class SeedingTests(SimpleTestCase):
    def test_standard_order(self):
        self.assertEqual(arena_tournament.seeding_order(2), [1, 2])
        self.assertEqual(arena_tournament.seeding_order(4), [1, 4, 2, 3])
        self.assertEqual(arena_tournament.seeding_order(8), [1, 8, 4, 5, 2, 7, 3, 6])

    def test_sizes(self):
        self.assertEqual([arena_tournament.bracket_size(n) for n in (2, 3, 4, 5, 8, 9, 33)], [2, 4, 4, 8, 8, 16, 64])
        self.assertEqual([arena_tournament.round_count(n) for n in (2, 3, 5, 16, 17)], [1, 2, 3, 4, 5])

    def test_every_seed_once(self):
        for size in (2, 4, 8, 16, 32, 64):
            self.assertEqual(sorted(arena_tournament.seeding_order(size)), list(range(1, size + 1)))


class GenerationTests(TestCase):
    fixtures = ['language_small.json']

    @classmethod
    def setUpTestData(cls):
        cls.organizer = organizer()
        cls.players = [player('p%02d' % i) for i in range(1, 34)]

    def build(self, count, third_place=False):
        tournament = arena_tournament.create_tournament(
            self.organizer.profile, 'Copa %d' % count, [u.profile for u in self.players[:count]],
            third_place=third_place)
        arena_tournament.generate_bracket(self.organizer.profile, tournament.pk)
        return ArenaTournament.objects.get(pk=tournament.pk)

    def test_every_size_from_2_to_33(self):
        for count in range(2, 34):
            with self.subTest(count=count):
                tournament = self.build(count)
                size = arena_tournament.bracket_size(count)
                matches = ArenaMatch.objects.filter(tournament=tournament)
                self.assertEqual(matches.count(), size - 1)
                first = matches.filter(round__number=1)
                byes = first.filter(decided_reason='bye')
                self.assertEqual(byes.count(), size - count)
                for match in byes:
                    present = match.seed_a if match.player_a_id else match.seed_b
                    self.assertLessEqual(present, size - count, 'a bye must go to one of the best seeds')
                    self.assertTrue(bool(match.player_a_id) != bool(match.player_b_id))
                    self.assertEqual(match.status, ArenaMatch.DECIDED)
                self.assertFalse(first.filter(player_a__isnull=True, player_b__isnull=True).exists())
                final = matches.get(next_match__isnull=True)
                self.assertEqual(final.round.number, arena_tournament.round_count(count))
                for match in matches.exclude(pk=final.pk):
                    self.assertEqual(match.next_match.round.number, match.round.number + 1)
                    self.assertEqual(match.next_slot, 'a' if match.position % 2 == 0 else 'b')
                # Every player shows up exactly once in the first round.
                seen = [pk for pair in first.values_list('player_a_id', 'player_b_id') for pk in pair if pk]
                self.assertEqual(sorted(seen), sorted(u.profile.pk for u in self.players[:count]))

    def test_byes_fill_the_second_round(self):
        tournament = self.build(3)
        final = ArenaMatch.objects.get(tournament=tournament, round__number=2)
        self.assertEqual((final.player_a.user.username, final.seed_a), ('p01', 1))
        self.assertIsNone(final.player_b_id)
        self.assertEqual(final.status, ArenaMatch.PENDING)

    def test_third_place_match(self):
        tournament = self.build(4, third_place=True)
        third = ArenaMatch.objects.get(tournament=tournament, is_third_place=True)
        semifinals = ArenaMatch.objects.filter(tournament=tournament, round__number=1)
        self.assertEqual(set(semifinals.values_list('loser_next_match_id', flat=True)), {third.pk})
        self.assertEqual(sorted(semifinals.values_list('loser_next_slot', flat=True)), ['a', 'b'])

    def test_rebuilding_starts_over(self):
        tournament = self.build(5)
        arena_tournament.generate_bracket(self.organizer.profile, tournament.pk)
        self.assertEqual(ArenaMatch.objects.filter(tournament=tournament).count(), 7)

    def test_names_of_rounds(self):
        names = [arena_tournament.default_round_name(n, 4) for n in (1, 2, 3, 4)]
        self.assertEqual(len(set(names)), 4)

    def test_random_and_rating_seeding(self):
        profiles = [u.profile for u in self.players[:6]]
        for index, profile in enumerate(profiles):
            profile.rating = 1000 + index * 100
            profile.save(update_fields=['rating'])
        rated = arena_tournament.create_tournament(self.organizer.profile, 'Por rating', profiles,
                                                   seeding=ArenaTournament.SEED_RATING)
        self.assertEqual(rated.participants.get(seed=1).profile_id, profiles[-1].pk)
        shuffled = arena_tournament.create_tournament(self.organizer.profile, 'Al azar', profiles,
                                                      seeding=ArenaTournament.SEED_RANDOM)
        self.assertEqual(sorted(shuffled.participants.values_list('seed', flat=True)), list(range(1, 7)))

    def test_limits(self):
        from judge.arena.engine import ArenaError
        with self.assertRaises(ArenaError):
            arena_tournament.create_tournament(self.organizer.profile, 'Solo', [self.players[0].profile])
        with self.assertRaises(ArenaError):
            arena_tournament.create_tournament(self.organizer.profile, '', [u.profile for u in self.players[:2]])
