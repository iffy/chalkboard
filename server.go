package main

import (
	//"database/sql"
	"strconv"
	"encoding/json"
	"fmt"
	_ "github.com/mattn/go-sqlite3"
	"log"
	"net/http"
	"path/filepath"
	"runtime"
	_ "time"
)


func f(fname string) func(w http.ResponseWriter, r *http.Request) {
	res := func(w http.ResponseWriter, r *http.Request) {
		log.Println(fname)
		log.Println(*r)
		http.ServeFile(w, r, fname)
	}
	return res
}

type Store interface {
	Itersticky(func (Sticky))
	AddSticky(Sticky) Sticky
	RmSticky(int) 
	UpdateSticky(Sticky)
}

type MemStore struct {
	stickies map[int]Sticky
	Id int
}

func (m *MemStore) Itersticky(fn func (Sticky)) {
	for _, v := range(m.stickies) {
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
}

type Sticky struct {
	Id   int 	`json:"id"`
	Note string	`json:"note"`
	X    int	`json:"x"`
	Y    int	`json:"y"`
}

func NewEv(s Store) Ev {
	return Ev{make(chan ResponseWriter), make(chan string), make(map[string]ResponseWriter), s}
}

type Ev struct {
	subscribed chan ResponseWriter
	events chan string
	clients    map[string]ResponseWriter
	Store
}

var files = []string{"/js/backbone-min.js", "/js/jquery-1.9.0.js",
	"/js/jquery-ui.js", "/js/underscore-min.js"}

func mkev(evt, data string) (res string) {
	res = fmt.Sprintf("event: %s\ndata: %s\n\n", evt, data)
	return
}

func (e *Ev) subscribe(w http.ResponseWriter) (done chan bool) {
	done = make(chan bool)
	r := ResponseWriter{done, w}
	e.subscribed <- r
	return done
}

func (e *Ev) work() {
	for {
		select {
		case rw := <-e.subscribed:
			s := fmt.Sprintf("%s", rw)
			e.clients[s] = rw
			go e.Itersticky(func (st Sticky) {
				a, _ := json.Marshal(st)
				e.events <- mkev("add", string(a))
			})				
			log.Printf("%s", rw.writer)
		case ev := <-e.events:
			for _, w := range e.clients {
				log.Printf("send event %s to %s", ev, w)
				fmt.Fprintf(w.writer, ev)
				flush(w.writer)
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
		log.Println("Flush")
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
	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case "POST":
			err := r.ParseForm()
			if err != nil {
				log.Println(e)
				return
			}
			log.Println(r.Form)
			formdat := r.Form
			switch formdat["action"][0] {
			case "add":
				log.Println("add!")
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
				e.events <- mkev("add", string(a))
			case "update":
				log.Println("update!")
				id, _ := strconv.Atoi(formdat["id"][0])
				note := formdat["note"][0]
				x, _ := strconv.Atoi(formdat["x"][0])
				y, _ := strconv.Atoi(formdat["y"][0])
				st := Sticky{id, note, x, y}
				e.UpdateSticky(st)
//				r := fmt.Sprintf(`"%d"`, id)
				a, _ := json.Marshal(st)
				e.events <- mkev("update", string(a))
			case "remove":
				log.Println("remove")
				id, _ := strconv.Atoi(formdat["id"][0])
				e.RmSticky(id)
				r := fmt.Sprintf(`"%d"`, id)
				e.events <- mkev("remove", r)
			}
		case "GET":
			http.ServeFile(w, r, "chalkboard.html")
		}

	})
	http.HandleFunc("/events", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-type", "text/event-stream")
		fmt.Fprintf(w, mkev("hello", `"keep alive"`))
		flush(w)
		done := e.subscribe(w)
		<-done
		log.Println("all done %s", *r)
	})
	log.Fatal(http.ListenAndServe(":8080", nil))
}
