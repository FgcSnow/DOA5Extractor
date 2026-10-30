import sys
import extracteur
import interface

if __name__ == "__main__":
    if len(sys.argv) == 1:
        interface.main()
    else:
        try:
            extracteur.cli()
        except Exception as exc:
            sys.stdout.flush()
            print("ERROR: " + extracteur.error_text(exc), file=sys.stderr)
            sys.exit(1)
