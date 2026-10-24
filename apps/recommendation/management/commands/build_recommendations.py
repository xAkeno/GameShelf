<<<<<<< HEAD
from django.core.management.base import BaseCommand

from apps.recommendation import jobs

=======

from django.core.management.base import BaseCommand

from apps.recommendation import jobs
>>>>>>> 205125e9e5dfac2f37b36d4af8e3adbfc783c38b
ORDER = ["bayesian", "tfidf", "svd", "kmeans"]


class Command(BaseCommand):
<<<<<<< HEAD
    help = "Rebuild the recommendation tables: Bayesian scores, TF-IDF similarities, SVD picks, K-Means clusters."

    def add_arguments(self, parser):
        parser.add_argument("--only", nargs="+", choices=ORDER, help="Run just these jobs (default: all four).")
        parser.add_argument("--min-votes", type=float, default=None, help="Bayesian: ratings a game needs before its own average counts fully.")
        parser.add_argument("--factors", type=int, default=20, help="SVD: number of hidden taste factors.")
        parser.add_argument("--top-n", type=int, default=20, help="Similar games and recommendations kept per game or player.")
        parser.add_argument("--clusters", type=int, default=None, help="K-Means: number of clusters (default: pick the best).")

    def handle(self, *args, **options):
        runners = {
            "bayesian": lambda: jobs.build_bayesian_scores(min_votes=options["min_votes"]),
            "tfidf": lambda: jobs.build_similarities(top_n=options["top_n"]),
            "svd": lambda: jobs.build_recommendations(factors=options["factors"], top_n=options["top_n"]),
            "kmeans": lambda: jobs.build_clusters(k=options["clusters"]),
        }
        for name in ORDER:
            if options["only"] and name not in options["only"]:
                continue
            run = runners[name]()
            if run.status == "succeeded":
                self.stdout.write(self.style.SUCCESS(f"{name}: done {run.metrics}"))
            else:
                self.stdout.write(self.style.WARNING(f"{name}: skipped - {run.error}"))
=======
    help = "Build GameShelf recommendation models and results."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only",
            nargs="+",
            choices=ORDER,
            help="Run selected algorithms (default: all four).",
        )
        parser.add_argument(
            "--min-votes",
            type=float,
            default=None,
            help="Bayesian: minimum rating weight threshold.",
        )
        parser.add_argument(
            "--factors",
            type=int,
            default=20,
            help="SVD: number of hidden taste factors.",
        )
        parser.add_argument(
            "--top-n",
            type=int,
            default=20,
            help="Number of recommendations or similar games to save.",
        )
        parser.add_argument(
            "--clusters",
            type=int,
            default=None,
            help="K-Means: number of clusters (default: automatic).",
        )

    def handle(self, *args, **options):
        runners = {
            "bayesian": lambda: jobs.build_bayesian_scores(
                min_votes=options["min_votes"]
            ),
            "tfidf": lambda: jobs.build_similarities(
                top_n=options["top_n"]
            ),
            "svd": lambda: jobs.build_recommendations(
                factors=options["factors"],
                top_n=options["top_n"],
            ),
            "kmeans": lambda: jobs.build_clusters(
                k=options["clusters"]
            ),
        }

        selected = options["only"] or ORDER

        for name in ORDER:
            if name not in selected:
                continue

            try:
                run = runners[name]()

                if run.status == "succeeded":
                    self.stdout.write(
                        self.style.SUCCESS(f"{name}: done {run.metrics}")
                    )
                elif run.status == "failed":
                    self.stderr.write(
                        self.style.ERROR(f"{name}: failed - {run.error}")
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(f"{name}: skipped - {run.error}")
                    )

            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"{name}: failed - {exc}")
                )
>>>>>>> 205125e9e5dfac2f37b36d4af8e3adbfc783c38b
