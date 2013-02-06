package main

import (
	//"database/sql"
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

type ResponseWriter struct {
	done   chan bool
	writer http.ResponseWriter
}

type Sticky struct {
	id   int
	note string
	x    int
	y    int
}

type Ev struct {
	subscribed chan ResponseWriter
	clients    map[string]ResponseWriter
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
			log.Println(rw)
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

func main() {
	useAllCores()
	cwd, err := filepath.Abs(".")
	if err != nil {
		log.Fatal(err)
	}
	for _, file := range files {

		http.HandleFunc(file, f(cwd+file))
	}
	e := Ev{make(chan ResponseWriter), make(map[string]ResponseWriter)}
	go e.work()
	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case "POST":
			e := r.ParseForm()
			if e != nil {
				log.Println(e)
				return
			}
			log.Println(r.Form)
			formdat := r.Form
			switch formdat["action"][0] {
			case "add":
				log.Println("add!")
			}
		case "GET":
			http.ServeFile(w, r, "chalkboard.html")
		}

	})
	http.HandleFunc("/events", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-type", "text/event-stream")
		fmt.Fprintf(w, mkev("hello", "keep alive"))
		done := e.subscribe(w)
		<-done
		log.Println("all done %s", *r)
	})
	log.Fatal(http.ListenAndServe(":8080", nil))
}
