document.addEventListener('DOMContentLoaded', () => {

    setTimeout(() => { document.getElementById("retry").click() }, 2 * 60 * 1000);

});

document.getElementById("retry").addEventListener("click", function (){
    window.location.href = '/';  
});
