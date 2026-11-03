while true; do
  clear
  echo "APE Memory Monitor - $(date '+%H:%M:%S')"
  echo "-------------------------------------------"

  ps -p 6317,32562,71686 -o pid=,rss= | awk '
  {
    if ($1 == 6317)  name = "Rust Author"
    if ($1 == 32562) name = "Rust Joiner"
    if ($1 == 71686) name = "C# Joiner"

    printf "%-15s %8.2f MiB\n", name, $2/1024
  }'

  sleep 2
done
