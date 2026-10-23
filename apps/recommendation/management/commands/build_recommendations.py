
from django.core.management.base import BaseCommand

from apps.recommendation import jobs
ORDER = ["bayesian", "tfidf", "svd", "kmeans"]


class Command(BaseCommand):
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
