package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"html/template"
	"log"
	"net/http"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"
)

type Item struct {
	ID    int    `json:"id"`
	Title string `json:"title"`
	Text  string `json:"text"`
	Kids  []int  `json:"kids"`
	Time  int64  `json:"time"`
}

type JobEntry struct {
	Kid         int
	Head        string
	Description string
	Day         string
	Time        string
	MonthYear   string
}

const hnItemURL = "https://hacker-news.firebaseio.com/v0/item/%d.json"

func getItem(id int) (*Item, error) {
	resp, err := http.Get(fmt.Sprintf(hnItemURL, id))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var item Item
	if err := json.NewDecoder(resp.Body).Decode(&item); err != nil {
		return nil, err
	}
	return &item, nil
}

func getThreadName(threadID int) (string, string, error) {
	item, err := getItem(threadID)
	if err != nil {
		return "", "", err
	}

	if strings.Contains(item.Title, "right now") {
		return "whoishiring_right_now", "right now", nil
	}

	re := regexp.MustCompile(`\(([A-Za-z]+ \d+)\)`)
	matches := re.FindStringSubmatch(item.Title)
	if len(matches) < 2 {
		return "", "", fmt.Errorf("could not parse month/year from title")
	}

	monthYear := strings.ToLower(matches[1])
	fileName := "whoishiring_" + strings.ReplaceAll(monthYear, " ", "_")
	return fileName, monthYear, nil
}

func parseComment(item *Item) *JobEntry {
	if item == nil || item.Text == "" {
		return nil
	}

	parts := strings.Split(item.Text, "<p>")
	if len(parts) < 1 {
		return nil
	}

	timestamp := time.Unix(item.Time, 0)
	dateStr := timestamp.Format("2006-01-02")
	timeStr := timestamp.Format("15:04:05")

	return &JobEntry{
		Kid:         item.ID,
		Head:        parts[0],
		Description: strings.Join(parts[1:], "<br>"),
		Day:         dateStr,
		Time:        timeStr,
	}
}

func processComments(kids []int, monthYear string) []*JobEntry {
	var (
		wg      sync.WaitGroup
		mu      sync.Mutex
		entries []*JobEntry
	)

	semaphore := make(chan struct{}, 20) // Ограничиваем количество горутин

	for _, kid := range kids {
		wg.Add(1)
		go func(kid int) {
			defer wg.Done()
			semaphore <- struct{}{}        // Занимаем слот
			defer func() { <-semaphore }() // Освобождаем слот

			item, err := getItem(kid)
			if err != nil {
				log.Printf("Error fetching item %d: %v", kid, err)
				return
			}

			if entry := parseComment(item); entry != nil {
				entry.MonthYear = monthYear
				mu.Lock()
				entries = append(entries, entry)
				mu.Unlock()
			}
		}(kid)
	}

	wg.Wait()
	return entries
}

func generateHTML(entries []*JobEntry, filename, monthYear string) error {
	const templateText = `
<!DOCTYPE html>
<html>
<head>
    <title>{{.Title}}</title>
    <style>
        .job_entry { margin-bottom: 20px; padding: 10px; border: 1px solid #ccc; }
        .job_head { font-weight: bold; margin-bottom: 10px; }
    </style>
</head>
<body>
    {{range .Jobs}}
        <div class="job_entry">
            <div class="job_head">
                <em>#{{.Number}}</em>
                {{.Head}}, posted: {{.Day}} at {{.Time}}
            </div>
            {{.Description}}
        </div>
    {{end}}
</body>
</html>`

	type JobView struct {
		Number      int
		Head        template.HTML
		Description template.HTML
		Day         string
		Time        string
	}

	data := struct {
		Title string
		Jobs  []JobView
	}{
		Title: filename,
		Jobs:  []JobView{},
	}

	counter := 0
	for i, entry := range entries {
		if entry.MonthYear != monthYear {
			continue
		}

		entryText := strings.ToLower(entry.Head + " " + entry.Description)
		if !strings.Contains(entryText, "remote") {
			continue
		}

		data.Jobs = append(data.Jobs, JobView{
			Number:      i + 1,
			Head:        template.HTML(entry.Head),
			Description: template.HTML(entry.Description),
			Day:         entry.Day,
			Time:        entry.Time,
		})
		counter++
	}

	tmpl, err := template.New("jobs").Parse(templateText)
	if err != nil {
		return err
	}

	f, err := os.Create(filename + ".html")
	if err != nil {
		return err
	}
	defer f.Close()

	if err := tmpl.Execute(f, data); err != nil {
		return err
	}

	fmt.Printf("Written to html: %d job postings.\n", counter)
	return nil
}

func main() {
	threadID := flag.Int("t", 0, "Who is hiring thread number")
	flag.Parse()

	if *threadID == 0 {
		log.Fatal("Thread ID is required. Use -t flag")
	}

	filename, monthYear, err := getThreadName(*threadID)
	if err != nil {
		log.Fatal(err)
	}

	item, err := getItem(*threadID)
	if err != nil {
		log.Fatal(err)
	}

	fmt.Printf("In thread %d with name %q are %d records\n", *threadID, filename, len(item.Kids))

	entries := processComments(item.Kids, monthYear)
	if err := generateHTML(entries, filename, monthYear); err != nil {
		log.Fatal(err)
	}
}
