package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"path/filepath"
	"runtime"
	"strconv"
)

func f(fname string) func(w http.ResponseWriter, r *http.Request) {
	res := func(w http.ResponseWriter, r *http.Request) {
		http.ServeFile(w, r, fname)
	}
	return res
}

type Store interface {
	Itersticky(func(Sticky))
	AddSticky(Sticky) Sticky
	RmSticky(int)
	UpdateSticky(Sticky)
}

type MemStore struct {
	stickies map[int]Sticky
	Id       int
}

func (m *MemStore) Itersticky(fn func(Sticky)) {
	for _, v := range m.stickies {
		fn(v)
	}
}

func (m *MemStore) AddSticky(a Sticky) Sticky {
	a.Id = m.Id
	m.stickies[a.Id] = a
	m.Id += 1
	return m.stickies[a.Id]
}

func (m *MemStore) RmSticky(id int) {
	delete(m.stickies, id)
}

func (m *MemStore) UpdateSticky(a Sticky) {
	m.stickies[a.Id] = a
}

type ResponseWriter struct {
	done   chan bool
	writer http.ResponseWriter
	req    http.Request
}

type Sticky struct {
	Id   int    `json:"id"`
	Note string `json:"note"`
	X    int    `json:"x"`
	Y    int    `json:"y"`
}

func NewEv(s Store) Ev {
	return Ev{make(chan ResponseWriter), make(chan string), make(map[string]ResponseWriter), s}
}

type Ev struct {
	subscribed chan ResponseWriter
	events     chan string
	clients    map[string]ResponseWriter
	Store	   //interface
}

var files = []string{"/js/backbone-min.js", "/js/jquery-1.9.0.js",
	"/js/jquery-ui.js", "/js/underscore-min.js"}

func mkevent(evt, data string) (res string) {
	res = fmt.Sprintf("event: %s\ndata: %s\n\n", evt, data)
	return
}

func (e *Ev) subscribe(w http.ResponseWriter, req http.Request) (done chan bool) {
	done = make(chan bool)
	r := ResponseWriter{done, w, req}
	e.subscribed <- r
	return done
}

func (e *Ev) work() {
	for {
		select {
		case rw := <-e.subscribed:
			e.clients[rw.req.RemoteAddr] = rw
			log.Printf("adding client %s", rw.req.RemoteAddr)
			e.Itersticky(func(st Sticky) {
				a, _ := json.Marshal(st)
				ev := mkevent("add", string(a))
				fmt.Fprintf(rw.writer, ev)
				flush(rw.writer)
			})
		case ev := <-e.events:
			log.Printf("Broadcast to %d clients", len(e.clients))
			var delthese []string = nil
			for k, w := range e.clients {
				_, err := fmt.Fprintf(w.writer, ev)
				if err != nil {
					delthese = append(delthese, k)
					w.done <- true
					log.Printf("removing client for %s", err)
				} else {
					flush(w.writer)
				}
			}
			for _, v := range(delthese) {
				delete(e.clients, v)
				
			}
		}
	}
}

func useAllCores() {
	ncpu := runtime.NumCPU()
	x := runtime.GOMAXPROCS(ncpu)
	if x == ncpu {
		log.Printf("Not increasing from %d cores", x)
	} else {
		log.Printf("Increasing cores from %d to %d", x, ncpu)
	}
}

func flush(w http.ResponseWriter) {
	// w happens to implement Flusher but 
	// you have to convert it like this because 
	// the compiler doesn't believe it.

	if fw, ok := w.(http.Flusher); ok {
		fw.Flush()
	}
}

func chalkboard(e Ev) func(http.ResponseWriter, *http.Request) {
return func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case "POST":
			err := r.ParseForm()
			if err != nil {
				log.Println(e)
				return
			}
			formdat := r.Form
			action := formdat["action"][0]
			switch action {
			case "add":
				note := formdat["note"][0]
				x, _ := strconv.Atoi(formdat["x"][0])
				y, _ := strconv.Atoi(formdat["y"][0])
				st := Sticky{0, note, x, y}
				st = e.AddSticky(st)
				a, err := json.Marshal(st)
				if err != nil {
					log.Printf("error: err")
				}
				w.Header().Set("Content-type", "application/json")
				fmt.Fprintf(w, string(a))
				flush(w)
				e.events <- mkevent("add", string(a))
			case "update":
				id, _ := strconv.Atoi(formdat["id"][0])
				note := formdat["note"][0]
				x, _ := strconv.Atoi(formdat["x"][0])
				y, _ := strconv.Atoi(formdat["y"][0])
				st := Sticky{id, note, x, y}
				e.UpdateSticky(st)
				a, _ := json.Marshal(st)
				e.events <- mkevent("update", string(a))
			case "remove":
				id, _ := strconv.Atoi(formdat["id"][0])
				e.RmSticky(id)
				r := fmt.Sprintf(`"%d"`, id)
				e.events <- mkevent("remove", r)
			}
		case "GET":
			http.ServeFile(w, r, "chalkboard.html")
		}

	}
}

func events(e Ev) func(http.ResponseWriter, *http.Request) {
return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-type", "text/event-stream")
		fmt.Fprintf(w, mkevent("hello", `"keep alive"`))
		flush(w)
		done := e.subscribe(w, *r)
		<-done
	}
}
func main() {
	useAllCores()
	cwd, err := filepath.Abs(".")
	if err != nil {
		log.Fatal(err)
	}
	for _, file := range files {

		http.HandleFunc(file, f(cwd+file))
	}
	store := &MemStore{make(map[int]Sticky), 1}
	e := NewEv(store)
	go e.work()
	http.HandleFunc("/", chalkboard(e))
	http.HandleFunc("/events", events(e))
	log.Fatal(http.ListenAndServe(":8080", nil))
}
