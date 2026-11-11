from init_db import main

if __name__ == "__main__":
    print("rebuilding dify_app tables and seed data...")
    main()
    print("reset done: players/events/pranks cleared, drinks/rumors restored")
